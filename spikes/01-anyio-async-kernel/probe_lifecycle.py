"""Probe 1: drive one kernel's whole lifecycle with AsyncKernelManager under AnyIO.

Questions: does every step work from inside anyio.run (asyncio backend), does
any step fall back into jupyter_core.run_sync, and does the process return to
its starting thread and file-descriptor counts afterwards?
"""

from __future__ import annotations

import json
import os
import time

import anyio
from harness import SYNC_BRIDGE, children, collect, new_manager, process_facts, remove_root, run_cell


async def main() -> None:
    import asyncio

    report: dict = {"loop": type(asyncio.get_running_loop()).__module__, "before": process_facts()}
    manager, root = new_manager()
    steps = {}

    t = time.perf_counter()
    await manager.start_kernel(cwd=str(root))
    client = manager.client()
    client.start_channels()
    await client.wait_for_ready(timeout=30)
    steps["start"] = round(time.perf_counter() - t, 2)
    report["running"] = process_facts()

    report["execute"] = await run_cell(client, "x = 41\nprint('hi')\nx + 1")
    report["help_page"] = await run_cell(client, "len?")

    t = time.perf_counter()
    msg = client.execute("import time; time.sleep(30)")
    await anyio.sleep(1)
    await manager.interrupt_kernel()
    report["interrupt"] = await collect(client, msg)
    steps["interrupt_roundtrip"] = round(time.perf_counter() - t, 2)

    t = time.perf_counter()
    await manager.restart_kernel(now=True)
    await client.wait_for_ready(timeout=30)
    steps["restart"] = round(time.perf_counter() - t, 2)
    report["after_restart"] = await run_cell(client, "'x' in dir()")
    report["alive"] = await manager.is_alive()

    t = time.perf_counter()
    client.stop_channels()
    # shutdown_kernel already awaits cleanup_resources; on AsyncKernelManager
    # that name is a coroutine, so a bare call silently does nothing.
    await manager.shutdown_kernel(now=True)
    remove_root(root)
    steps["shutdown"] = round(time.perf_counter() - t, 2)

    report["steps_seconds"] = steps
    report["after"] = process_facts()
    report["children_left"] = children()
    report["sync_bridge_calls"] = dict(SYNC_BRIDGE)
    print(json.dumps(report, indent=1, ensure_ascii=False))


# uvicorn runs on uvloop whenever it is installed (loop="auto"), and
# zmq.asyncio needs the loop's add_reader family; SPIKE_UVLOOP=1 checks that loop.
anyio.run(main, backend="asyncio", backend_options={"use_uvloop": os.environ.get("SPIKE_UVLOOP") == "1"})
