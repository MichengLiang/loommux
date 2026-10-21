"""Probe 3: does a waiting tool call hold a thread, and can waits starve the server?

A (real server, the architecture before the migration): every MCP tool runs
its session operation through ``asyncio.to_thread`` (mcp/factory.py), so a
``wait`` holds a default-executor worker for as long as it waits. N clients
wait on one shared kernel that is sleeping; another client then calls
``status`` on its own, already provisioned, private kernel, and the console is
polled. Usage: ``probe_thread_pool.py a URL N``.

B (a model, not loommux): the post-migration shape of a wait, an
``anyio.Event`` under ``move_on_after``, with 1000 concurrent waiters and a
bystander measuring how late its sleeps wake. Usage: ``probe_thread_pool.py b``.
"""

from __future__ import annotations

import json
import sys
import threading
import time

import anyio
import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport


def mcp_client(url: str, resource: str | None = None) -> Client:
    headers = {"X-Loommux-Resource": resource} if resource else {}
    return Client(StreamableHttpTransport(f"{url}/mcp", headers=headers), timeout=180)


async def side_a(url: str, waiters: int) -> dict:
    async with mcp_client(url, "pool") as sleeper, mcp_client(url) as bystander:
        await bystander.call_tool("status", {})  # provision its private kernel up front
        await sleeper.call_tool("run_cell", {"freeform": "%%loommux --wait 1\nimport time; time.sleep(100)"})
        timings: dict = {}

        async def wait_on_pool() -> None:
            async with mcp_client(url, "pool") as client:
                await client.call_tool("wait", {"timeout_seconds": 60})

        async def observe() -> None:
            await anyio.sleep(5)  # every waiter is inside its wait by now
            async with httpx.AsyncClient(trust_env=False, timeout=90) as http:
                t = time.perf_counter()
                await http.get(f"{url}/api/resources")
                timings["console_seconds"] = round(time.perf_counter() - t, 2)
            t = time.perf_counter()
            await bystander.call_tool("status", {})
            timings["bystander_status_seconds"] = round(time.perf_counter() - t, 2)

        async with anyio.create_task_group() as tg:
            for _ in range(waiters):
                tg.start_soon(wait_on_pool)
            tg.start_soon(observe)
        await sleeper.call_tool("interrupt", {})
    return {"waiters": waiters, **timings}


async def side_b(waiters: int = 1000) -> dict:
    done = anyio.Event()
    lateness: list[float] = []

    async def wait_like_a_tool() -> None:
        with anyio.move_on_after(3):
            await done.wait()

    async def bystander() -> None:
        for _ in range(20):
            t = time.perf_counter()
            await anyio.sleep(0.05)
            lateness.append(time.perf_counter() - t - 0.05)

    async with anyio.create_task_group() as tg:
        for _ in range(waiters):
            tg.start_soon(wait_like_a_tool)
        tg.start_soon(bystander)
    return {"waiters": waiters, "threads": threading.active_count(),
            "bystander_max_lateness_ms": round(max(lateness) * 1000, 2)}


if __name__ == "__main__":
    if sys.argv[1] == "a":
        print(json.dumps(anyio.run(side_a, sys.argv[2], int(sys.argv[3]))))
    else:
        print(json.dumps(anyio.run(side_b)))
