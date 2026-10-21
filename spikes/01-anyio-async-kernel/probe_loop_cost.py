"""Probe 5: how long each execution-record operation would hold the event loop.

Before the migration these run on the iopub collector thread or in
``asyncio.to_thread``; after it, appends happen where iopub messages are
awaited, and reads where tool calls are awaited, both on the loop. Each
number is one call's wall time, which is how long every other request would
wait behind it. Uses the execution package of the working tree.

    python probe_loop_cost.py
"""

from __future__ import annotations

import json
import time

from loommux.execution import Execution

LINE = "2026-10-22 01:23:45 INFO worker-07 processed batch 1234 in 0.031 s ok\n"  # 72 chars


def timed(fn) -> float:
    t = time.perf_counter()
    fn()
    return round((time.perf_counter() - t) * 1000, 1)


def measure(total_mb: int, chunk_kb: int) -> dict:
    chunk = LINE * max(1, chunk_kb * 1024 // len(LINE))
    record = Execution(execution=1, kernel_pid=0)
    appends = []
    while record.logs.combined.character_count < total_mb * 1024 * 1024:
        appends.append(timed(lambda: record.append_stdout(chunk)))
    record.finish()
    return {
        "total_mb": total_mb,
        "chunk_kb": chunk_kb,
        "append_per_chunk_ms_max": max(appends),
        "append_total_ms": round(sum(appends)),
        "first_snapshot_ms": timed(record.snapshot),  # joins, splits lines, counts tokens
        "second_snapshot_ms": timed(record.snapshot),
        "status_snapshot_ms": timed(record.status_snapshot),
        "read_tail_20_ms": timed(lambda: record.logs.combined.read("-20:")),
        "read_full_ms": timed(lambda: record.logs.combined.read(None)),
        "search_literal_ms": timed(lambda: record.logs.combined.search("batch 1234 in 0.0", query_mode="literal")),
        "search_regex_ms": timed(lambda: record.logs.combined.search(r"worker-\d+ processed", query_mode="regex")),
    }


results = [measure(mb, kb) for mb, kb in ((1, 4), (1, 64), (10, 64), (10, 1024), (40, 1024))]
print(json.dumps(results, indent=1))
