"""Probe 1: what one Hypermorph transaction per output chunk costs.

Run one case per process (``python probe_commit_cost.py '<json>'``) so peak RSS
belongs to that case alone. Cases:

* ``ingest``: a source appends ``total_mb`` of output in ``chunk_kb`` chunks to a
  TextExecution (body in state) or CountedExecution (summary only), optionally
  with observers. Measures per-commit cost, event-loop lag, how long an exclusive
  command waits behind the stream, and the newcomer's baseline cost.
* ``neighbour``: commits a one-bit change to a Resource whose state also holds
  ``entries`` execution summaries — does the rest of the state tax each commit?
* ``objects``: opens ``count`` executions as separate objects.
"""

from __future__ import annotations

import json
import resource
import sys
import time

import anyio
from domain import CountedExecution, Resource, TextExecution
from hypermorph import Command, Runtime


def peak_mib() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def current_mib() -> float:
    # ru_maxrss is a high-water mark already set by the imports, so growth of
    # small objects needs the resident size now.
    with open("/proc/self/statm") as statm:
        return int(statm.read().split()[1]) * resource.getpagesize() / 2**20


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


async def observer(runtime: Runtime, frames: list[int]) -> None:
    async for snapshot in runtime.observe("e"):
        frames[0] += 1
        if snapshot.state["status"] != "running":
            return


def ms(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)
    return {
        "p50": round(ordered[len(ordered) // 2] * 1000, 3),
        "p99": round(ordered[int(len(ordered) * 0.99)] * 1000, 3),
        "max": round(ordered[-1] * 1000, 3),
    }


async def ingest(variant: str, total_mb: int, chunk_kb: int, observers: int, budget: float) -> dict:
    chunk = "x" * (chunk_kb * 1024 - 1) + "\n"
    planned = total_mb * 1024 // chunk_kb
    obj = TextExecution() if variant == "text" else CountedExecution()
    runtime = Runtime()
    runtime.open("e", obj)
    sent = 0
    deadline = time.perf_counter() + budget

    async def source():
        nonlocal sent
        for _ in range(planned):
            if time.perf_counter() > deadline:
                return
            sent += 1
            yield chunk

    lags: list[float] = []
    waits: list[float] = []
    frames = [0]
    stop = anyio.Event()
    async with runtime, anyio.create_task_group() as tasks:
        tasks.start_soon(lag_meter, stop, lags)
        tasks.start_soon(poker, runtime, stop, waits)
        for _ in range(observers):
            tasks.start_soon(observer, runtime, frames)
        await anyio.sleep(0.05)
        started = time.perf_counter()
        await runtime.attach(source(), into=obj._on_chunk, on_complete=obj._done).wait()
        elapsed = time.perf_counter() - started
        stop.set()
        started = time.perf_counter()
        snapshot = await runtime.snapshot("e")
        baseline = time.perf_counter() - started
    return {
        "case": "ingest",
        "variant": variant,
        "total_mb": total_mb,
        "chunk_kb": chunk_kb,
        "observers": observers,
        "planned": planned,
        "sent": sent,
        "complete": sent == planned,
        "seconds": round(elapsed, 3),
        "us_per_commit": round(elapsed / sent * 1e6, 1),
        "mb_per_s": round(sent * chunk_kb / 1024 / elapsed, 1),
        "version": snapshot.version,
        "observer_frames": frames[0],
        "loop_lag_ms": ms(lags),
        "poke_wait_ms": ms(waits),
        "baseline_ms": round(baseline * 1000, 3),
        "peak_rss_mib": round(peak_mib(), 1),
    }


async def neighbour(entries: int, commits: int) -> dict:
    obj = Resource()
    runtime = Runtime()
    runtime.open("r", obj)
    async with runtime:
        started = time.perf_counter()
        await runtime.submit(obj._fill, entries)
        fill = time.perf_counter() - started

        async def ticks():
            for _ in range(commits):
                yield None

        started = time.perf_counter()
        await runtime.attach(ticks(), into=obj._tick).wait()
        elapsed = time.perf_counter() - started
        version = (await runtime.snapshot("r")).version
    return {
        "case": "neighbour",
        "entries": entries,
        "commits": commits,
        "fill_ms": round(fill * 1000, 1),
        "us_per_commit": round(elapsed / commits * 1e6, 1),
        "version": version,
        "peak_rss_mib": round(peak_mib(), 1),
    }


async def objects(count: int) -> dict:
    before = current_mib()
    runtime = Runtime()
    async with runtime:
        started = time.perf_counter()
        for index in range(count):
            runtime.open(f"e{index}", CountedExecution())
        opened = time.perf_counter() - started
        started = time.perf_counter()
        listed = len(runtime.objects())
        listing = time.perf_counter() - started
        started = time.perf_counter()
        await runtime.snapshot(f"e{count // 2}")
        one = time.perf_counter() - started
        after_open = current_mib()
        started = time.perf_counter()
        for index in range(count):
            await runtime.close_object(f"e{index}")
        closed = time.perf_counter() - started
    return {
        "case": "objects",
        "count": count,
        "open_us_each": round(opened / count * 1e6, 1),
        "objects_listing_ms": round(listing * 1000, 1),
        "listed": listed,
        "snapshot_one_ms": round(one * 1000, 3),
        "close_us_each": round(closed / count * 1e6, 1),
        "rss_growth_mib": round(after_open - before, 1),
        "kib_per_object": round((after_open - before) * 1024 / count, 1),
    }


async def main(case: dict) -> None:
    kind = case.pop("case")
    result = await {"ingest": ingest, "neighbour": neighbour, "objects": objects}[kind](**case)
    print(json.dumps(result))


if __name__ == "__main__":
    anyio.run(main, json.loads(sys.argv[1]))
