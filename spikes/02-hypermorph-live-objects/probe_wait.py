"""Probe 4: MCP ``wait`` as observation until a terminal state or a deadline.

Today ``wait`` and ``run_cell --wait`` block on a per-execution condition. After
the migration they observe the Execution object until its status is terminal,
under ``move_on_after``. This probe checks that the observable semantics stay:
a deadline returns the current state without stopping the execution, a finished
execution answers at once, a waiter that goes away leaves nothing behind, and a
recycled execution ends the wait. It also counts the observation registrations
(``Runtime._watches``) an abandoned ``async for`` leaves until collection.

``python probe_wait.py``
"""

from __future__ import annotations

import gc
import json
import time
from contextlib import aclosing

import anyio
from domain import TERMINAL, CountedExecution
from hypermorph import Runtime


async def wait(runtime: Runtime, object_id: str, timeout: float) -> tuple[str, dict | None]:
    """The shape ``wait`` takes: terminal, timeout (still running), or closed (recycled)."""
    last = None
    with anyio.move_on_after(timeout):
        async with aclosing(runtime.observe(object_id)) as states:
            async for snapshot in states:
                last = dict(snapshot.state)
                if last["status"] in TERMINAL:
                    return "terminal", last
        return "closed", last
    return "timeout", last


async def leaky_wait(runtime: Runtime, object_id: str) -> None:
    async for snapshot in runtime.observe(object_id):
        if snapshot.state["status"] in TERMINAL:
            return


async def output(chunks: int, interval: float):
    for index in range(chunks):
        await anyio.sleep(interval)
        yield f"line {index}\n"


def started(runtime: Runtime, object_id: str, chunks: int, interval: float):
    execution = CountedExecution()
    runtime.open(object_id, execution)
    return execution, runtime.attach(output(chunks, interval), into=execution._on_chunk, on_complete=execution._done)


async def main() -> None:
    report: dict = {}
    runtime = Runtime()
    async with runtime:
        # A: deadline first; the execution keeps going and is waited again later.
        _, task = started(runtime, "a", chunks=20, interval=0.05)
        t = time.perf_counter()
        first = await wait(runtime, "a", 0.3)
        first_s = time.perf_counter() - t
        second = await wait(runtime, "a", 5)
        await task.wait()
        report["A_timeout_then_complete"] = {"first": first[0], "first_lines": first[1]["lines"], "first_s": round(first_s, 3), "second": second[0], "second_lines": second[1]["lines"]}

        # B: already finished.
        t = time.perf_counter()
        done = await wait(runtime, "a", 5)
        report["B_finished_ms"] = {"result": done[0], "ms": round((time.perf_counter() - t) * 1000, 3)}

        # C: the waiter goes away mid-wait (MCP client disconnect).
        _, task = started(runtime, "c", chunks=10, interval=0.05)
        with anyio.move_on_after(0.1):
            await wait(runtime, "c", 30)
        watches_after_cancel = len(runtime._watches)
        await task.wait()
        report["C_waiter_cancelled"] = {"watches_after_cancel": watches_after_cancel, "final": (await runtime.snapshot("c")).state["status"]}

        # D: recycled while waited on.
        _, task = started(runtime, "d", chunks=1000, interval=0.05)
        results: list = []

        async def waiter() -> None:
            results.append(await wait(runtime, "d", 30))

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(waiter)
            await anyio.sleep(0.2)
            t = time.perf_counter()
            await runtime.close_object("d")
            close_ms = (time.perf_counter() - t) * 1000
        report["D_recycled"] = {"wait": results[0][0], "last_status": results[0][1]["status"], "close_ms": round(close_ms, 2), "source": str(await task.wait())}

        # E: many waiters on one execution.
        _, task = started(runtime, "e", chunks=20, interval=0.05)
        lags: list[float] = []
        woke: list[float] = []
        stop = anyio.Event()

        async def lag_meter() -> None:
            while not stop.is_set():
                s = time.perf_counter()
                await anyio.sleep(0.001)
                lags.append(time.perf_counter() - s - 0.001)

        async def many() -> None:
            outcome = await wait(runtime, "e", 30)
            assert outcome[0] == "terminal"
            woke.append(time.perf_counter())

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(lag_meter)
            for _ in range(1000):
                tasks.start_soon(many)
            await task.wait()
            finished_at = time.perf_counter()
            while len(woke) < 1000:
                await anyio.sleep(0.001)
            stop.set()
        report["E_1000_waiters"] = {"all_woken_after_ms": round((max(woke) - finished_at) * 1000, 1), "loop_lag_max_ms": round(max(lags) * 1000, 2), "watches_after": len(runtime._watches)}

        # F: early exits — async for + return, versus aclosing.
        _, task = started(runtime, "f", chunks=1, interval=0.01)
        await task.wait()
        gc.disable()
        try:
            for _ in range(2000):
                await leaky_wait(runtime, "f")
            leaky = len(runtime._watches)
            gc.collect()
            await anyio.sleep(0.05)
            leaky_after_gc = len(runtime._watches)
            for _ in range(2000):
                await wait(runtime, "f", 5)
            closing = len(runtime._watches) - leaky_after_gc
        finally:
            gc.enable()
        report["F_registrations_after_2000_waits"] = {"async_for_return": leaky, "after_gc_collect": leaky_after_gc, "aclosing": closing}
    print(json.dumps(report))


if __name__ == "__main__":
    anyio.run(main)
