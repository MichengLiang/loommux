"""Probe 2: churn and cancellation, the two things structured concurrency leans on.

(a) churn: many kernels started, used, and shut down concurrently; does the
    process return to its baseline fds, threads, and children?
(b) cancel during start: is a kernel process orphaned when the start is
    cancelled, and does a shielded cleanup recover it?
(c) cancel during receive: is a message lost when get_iopub_msg is cancelled
    while waiting, and does the channel keep working?
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time

import anyio
from harness import SYNC_BRIDGE, children, collect, new_manager, process_facts, remove_root

CHURN = int(sys.argv[1]) if len(sys.argv) > 1 else 120


async def one_kernel(i: int) -> None:
    manager, root = new_manager()
    try:
        await manager.start_kernel(cwd=str(root))
        client = manager.client()
        client.start_channels(hb=False)
        try:
            await client.wait_for_ready(timeout=60)
            out = await collect(client, client.execute(f"print({i})"))
            assert out["stream"] == f"{i}\n", out
        finally:
            client.stop_channels()
    finally:
        with anyio.CancelScope(shield=True):
            await manager.shutdown_kernel(now=True)
        remove_root(root)


async def churn() -> dict:
    before = process_facts()
    t = time.perf_counter()
    limiter = anyio.CapacityLimiter(8)

    async def bounded(i: int) -> None:
        async with limiter:
            await one_kernel(i)

    async with anyio.create_task_group() as tg:
        for i in range(CHURN):
            tg.start_soon(bounded, i)
    return {"kernels": CHURN, "seconds": round(time.perf_counter() - t, 1), "before": before,
            "after": process_facts(), "children_left": children()}


async def cancelled_start(delay: float, shielded_cleanup: bool) -> dict:
    before = process_facts()
    manager, root = new_manager()
    with anyio.move_on_after(delay) as scope:
        try:
            await manager.start_kernel(cwd=str(root))
            await anyio.sleep(5)  # past the start: cancel lands here instead
        finally:
            if shielded_cleanup:
                with anyio.CancelScope(shield=True):
                    if manager.has_kernel:
                        await manager.shutdown_kernel(now=True)
    await anyio.sleep(0.3)
    orphans = children()
    leaked_fds = process_facts()["fds"] - before["fds"]
    for pid in orphans:  # tidy up whatever the naive variant leaked
        os.kill(pid, signal.SIGKILL)
    await anyio.sleep(0.1)
    children()  # reap them
    if not shielded_cleanup and manager.has_kernel:
        await manager.shutdown_kernel(now=True)  # release its sockets so the next case starts clean
    remove_root(root)
    return {"delay": delay, "shielded_cleanup": shielded_cleanup, "cancelled": scope.cancelled_caught,
            "orphans": len(orphans), "leaked_fds": leaked_fds}


async def cancelled_receive() -> dict:
    manager, root = new_manager()
    await manager.start_kernel(cwd=str(root))
    client = manager.client()
    client.start_channels(hb=False)
    await client.wait_for_ready(timeout=60)
    msg_id = client.execute("for i in range(2000): print(i, flush=True)")
    text, cancels, idle = "", 0, False
    deadline = time.monotonic() + 60
    while not idle and time.monotonic() < deadline:
        with anyio.move_on_after(0.0005) as scope:
            msg = await client.get_iopub_msg()
        if scope.cancelled_caught:
            cancels += 1
            continue
        if msg["parent_header"].get("msg_id") != msg_id:
            continue
        if msg["msg_type"] == "stream":
            text += msg["content"]["text"]
        idle = msg["msg_type"] == "status" and msg["content"]["execution_state"] == "idle"
    lines = text.split()
    after = await collect(client, client.execute("print('still works')"))
    client.stop_channels()
    await manager.shutdown_kernel(now=True)
    remove_root(root)
    return {"cancels": cancels, "lines_received": len(lines), "lines_expected": 2000,
            "in_order": lines == [str(i) for i in range(2000)], "channel_after": after["stream"].strip()}


async def main() -> None:
    report = {"churn": await churn()}
    report["cancelled_start"] = [
        await cancelled_start(delay, shielded)
        for shielded in (False, True)
        for delay in (0.0, 0.02, 0.1, 0.3)
    ]
    report["cancelled_receive"] = await cancelled_receive()
    report["final"] = process_facts()
    report["sync_bridge_calls"] = dict(SYNC_BRIDGE)
    print(json.dumps(report, indent=1))


anyio.run(main, backend="asyncio")
