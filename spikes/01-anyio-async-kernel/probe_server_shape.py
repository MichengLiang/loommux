"""Probe 6: the post-migration shape on a real uvicorn server.

One Starlette app whose lifespan task group owns a kernel, the task that
reads its iopub, and a task that waits for the kernel process to exit through
a pidfd (Linux), so nothing polls and no thread waits. Requests execute and
wait under move_on_after. Checks: 40 concurrent waiting requests beside a
quick one; a kernel killed from outside is noticed at once; server shutdown
cancels the group, the kernel is shut down in a shielded scope, and the
process returns to its baseline.

    python probe_server_shape.py
"""

from __future__ import annotations

import json
import os
import signal
import socket
import time
from contextlib import asynccontextmanager

import anyio
import httpx
import uvicorn
from harness import SYNC_BRIDGE, children, new_manager, process_facts, remove_root
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

STATE: dict = {"cells": {}, "exited_at": None}


class Cell:
    def __init__(self) -> None:
        self.text = ""
        self.done = anyio.Event()


async def read_iopub(client) -> None:
    while True:
        msg = await client.get_iopub_msg()
        cell = STATE["cells"].get(msg["parent_header"].get("msg_id"))
        if cell is None:
            continue
        if msg["msg_type"] == "stream":
            cell.text += msg["content"]["text"]
        elif msg["msg_type"] == "status" and msg["content"]["execution_state"] == "idle":
            cell.done.set()


async def watch_exit(pid: int) -> None:
    pidfd = os.pidfd_open(pid)
    try:
        await anyio.wait_readable(pidfd)
        STATE["exited_at"] = time.monotonic()
    finally:
        os.close(pidfd)


@asynccontextmanager
async def lifespan(_app):
    manager, root = new_manager()
    STATE["manager"] = manager
    try:
        async with anyio.create_task_group() as tg:
            await manager.start_kernel(cwd=str(root))
            client = manager.client()
            client.start_channels(hb=False)
            await client.wait_for_ready(timeout=60)
            STATE["client"] = client
            STATE["kernel_pid"] = manager.provisioner.pid
            tg.start_soon(read_iopub, client)
            tg.start_soon(watch_exit, manager.provisioner.pid)
            try:
                yield
            finally:
                tg.cancel_scope.cancel()
                client.stop_channels()
    finally:
        with anyio.CancelScope(shield=True):
            if manager.has_kernel:
                await manager.shutdown_kernel(now=True)
        remove_root(root)


async def run(request):
    code = request.query_params["code"]
    wait = float(request.query_params.get("wait", "10"))
    cell = Cell()
    msg_id = STATE["client"].execute(code)
    STATE["cells"][msg_id] = cell
    with anyio.move_on_after(wait):
        await cell.done.wait()
    return JSONResponse({"done": cell.done.is_set(), "text": cell.text})


app = Starlette(routes=[Route("/run", run)], lifespan=lifespan)


async def main() -> dict:
    baseline = process_facts()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    report: dict = {"baseline": baseline}
    async with anyio.create_task_group() as tg:
        tg.start_soon(server.serve)
        while not server.started:
            await anyio.sleep(0.05)
        url = f"http://127.0.0.1:{port}/run"
        async with httpx.AsyncClient(trust_env=False, timeout=60) as http:
            # One kernel runs one cell at a time, so the waiters queue in the kernel;
            # the question is whether the server itself stays responsive meanwhile.
            await http.get(url, params={"code": "import time"})
            slow = [None] * 40

            async def waiter(i: int) -> None:
                slow[i] = (await http.get(url, params={"code": "time.sleep(60)", "wait": "2"})).json()["done"]

            t = time.perf_counter()
            async with anyio.create_task_group() as waiters:
                for i in range(40):
                    waiters.start_soon(waiter, i)
                await anyio.sleep(0.5)
                q = time.perf_counter()
                # The kernel is busy, so a cell would queue; probe the server, not the kernel.
                await http.get(f"http://127.0.0.1:{port}/missing")
                report["quick_request_ms_during_40_waits"] = round((time.perf_counter() - q) * 1000, 1)
            report["forty_waits_seconds"] = round(time.perf_counter() - t, 2)
            report["waits_returned_running"] = slow.count(False)
            report["threads_during_load"] = process_facts()["threads"]

            STATE["manager"].provisioner.process.send_signal(signal.SIGKILL)
            killed = time.monotonic()
            with anyio.fail_after(5):
                while STATE["exited_at"] is None:
                    await anyio.sleep(0.01)
            report["kernel_death_noticed_ms"] = round((STATE["exited_at"] - killed) * 1000, 1)
        server.should_exit = True
    await anyio.sleep(0.3)
    report["after_shutdown"] = process_facts()
    report["children_left"] = children()
    report["sync_bridge_calls"] = dict(SYNC_BRIDGE)
    return report


print(json.dumps(anyio.run(main), indent=1))
