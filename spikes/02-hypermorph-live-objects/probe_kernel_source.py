"""Probe 2: a real kernel's iopub as a Hypermorph source.

``python probe_kernel_source.py '<json>'`` with ``{"mode": "raw"|"live",
"shape": "lines"|"blocks", "total_mb": N}`` measures how large the chunks a
kernel actually sends are, and what the Runtime path adds over reading iopub
directly; ``{"mode": "interrupt"}`` measures how long an interrupt takes to
reach a terminal Execution while the kernel floods output.

The kernel is launched the way spike 01 launched it (private root, IPC, no
heartbeat channel), through spike 01's harness.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import anyio
from domain import CountedExecution
from hypermorph import Command, Runtime

sys.path.insert(0, str(Path(__file__).parents[1] / "01-anyio-async-kernel"))
from harness import children, new_manager, remove_root  # noqa: E402

PRODUCERS = {
    # ipykernel buffers small writes and flushes on its own schedule.
    "lines": "import sys\nline = 'y' * 99 + '\\n'\nfor _ in range({count}): sys.stdout.write(line)\n",
    "blocks": "import sys\nblock = ('z' * 1023 + '\\n') * 1024\nfor _ in range({count}): sys.stdout.write(block)\n",
}
FLOOD = "import sys\nline = 'w' * 99 + '\\n'\nwhile True: sys.stdout.write(line)\n"


class KernelExecution(CountedExecution):
    def _on_msg(self, item: tuple[str, str]) -> None:
        kind, value = item
        if kind == "stream":
            self._on_chunk(value)
        elif kind == "error":
            self.status = "interrupted" if value == "KeyboardInterrupt" else "error"


async def iopub(client, msg_id: str, sizes: list[int]) -> AsyncIterator[tuple[str, str]]:
    while True:
        msg = await client.get_iopub_msg()
        if msg["parent_header"].get("msg_id") != msg_id:
            continue
        kind, content = msg["msg_type"], msg["content"]
        if kind == "stream":
            sizes.append(len(content["text"]))
            yield ("stream", content["text"])
        elif kind == "error":
            yield ("error", content["ename"])
        elif kind == "status" and content["execution_state"] == "idle":
            return


def summary(sizes: list[int]) -> dict:
    return {
        "chunks": len(sizes),
        "chunk_kib_median": round(statistics.median(sizes) / 1024, 1),
        "chunk_kib_max": round(max(sizes) / 1024, 1),
    }


async def lag_meter(stop: anyio.Event, lags: list[float]) -> None:
    while not stop.is_set():
        started = time.perf_counter()
        await anyio.sleep(0.001)
        lags.append(time.perf_counter() - started - 0.001)


async def poker(runtime: Runtime, stop: anyio.Event, waits: list[float]) -> None:
    while not stop.is_set():
        started = time.perf_counter()
        await runtime.invoke(Command("e", "poke"))
        waits.append(time.perf_counter() - started)
        await anyio.sleep(0.05)


async def follow(runtime: Runtime, frames: list[int]) -> None:
    async for snapshot in runtime.observe("e"):
        frames[0] += 1
        if snapshot.state["status"] != "running":
            return


def worst_ms(values: list[float]) -> float:
    return round(max(values) * 1000, 2) if values else 0.0


async def with_kernel(body):
    manager, root = new_manager()
    await manager.start_kernel()
    client = manager.client()
    client.start_channels(hb=False)
    try:
        await client.wait_for_ready(timeout=60)
        return await body(manager, client)
    finally:
        with anyio.CancelScope(shield=True):
            client.stop_channels()
            await manager.shutdown_kernel(now=True)
            remove_root(root)


async def measured(mode: str, shape: str, total_mb: int) -> dict:
    unit = 100 if shape == "lines" else 1024 * 1024
    code = PRODUCERS[shape].format(count=total_mb * 1024 * 1024 // unit)

    async def body(manager, client) -> dict:
        sizes: list[int] = []
        started = time.perf_counter()
        if mode == "raw":
            received = 0
            async for kind, value in iopub(client, client.execute(code), sizes):
                if kind == "stream":
                    received += len(value)
            extra = {"received_mib": round(received / 2**20, 1)}
        else:
            execution = KernelExecution()
            runtime = Runtime()
            runtime.open("e", execution)
            lags: list[float] = []
            waits: list[float] = []
            frames = [0]
            stop = anyio.Event()
            async with runtime, anyio.create_task_group() as tasks:
                tasks.start_soon(lag_meter, stop, lags)
                tasks.start_soon(poker, runtime, stop, waits)
                tasks.start_soon(follow, runtime, frames)
                source = iopub(client, client.execute(code), sizes)
                await runtime.attach(source, into=execution._on_msg, on_complete=execution._done).wait()
                stop.set()
                final = await runtime.snapshot("e")
            extra = {
                "received_mib": round(final.state["chars"] / 2**20, 1),
                "commits": final.version,
                "observer_frames": frames[0],
                "loop_lag_max_ms": worst_ms(lags),
                "poke_wait_max_ms": worst_ms(waits),
            }
        seconds = time.perf_counter() - started
        return {"mode": mode, "shape": shape, "total_mb": total_mb, "seconds": round(seconds, 3), "mb_per_s": round(total_mb / seconds, 1), **summary(sizes), **extra}

    return await with_kernel(body)


async def interrupt() -> dict:
    async def body(manager, client) -> dict:
        execution = KernelExecution()
        runtime = Runtime()
        runtime.open("e", execution)
        sizes: list[int] = []
        async with runtime:
            task = runtime.attach(iopub(client, client.execute(FLOOD), sizes), into=execution._on_msg, on_complete=execution._done)
            await anyio.sleep(2)
            started = time.perf_counter()
            # The command only records intent; the async interrupt happens outside the transaction.
            await runtime.invoke(Command("e", "poke"))
            command = time.perf_counter() - started
            await manager.interrupt_kernel()
            with anyio.fail_after(30):
                await task.wait()
            ended = time.perf_counter() - started
            final = await runtime.snapshot("e")
        return {"mode": "interrupt", "status": final.state["status"], "flood_mib": round(final.state["chars"] / 2**20, 1), "command_ms": round(command * 1000, 2), "to_terminal_ms": round(ended * 1000, 1), **summary(sizes)}

    return await with_kernel(body)


async def main(case: dict) -> None:
    before = children()
    result = await (interrupt() if case["mode"] == "interrupt" else measured(case["mode"], case["shape"], case["total_mb"]))
    result["leftover_children"] = len(set(children()) - set(before))
    print(json.dumps(result))


if __name__ == "__main__":
    anyio.run(main, json.loads(sys.argv[1]))
