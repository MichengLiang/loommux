"""Load an isolated loommux server, then check that it gives back what it took.

Run from the repository root::

    uv run python scripts/stress.py --seconds 180

The script starts its own HTTP server on a free loopback port, in a temporary
workspace, with a 0.2 s sweep, 1 s connection and orphan grace, and a 1 s
named window, which departed sessions' main kernels wait in, so that leases
expire, resources are orphaned, and kernels are retired continuously.
Private and shared clients then call random tools, cancel calls midway, and
disconnect at random while a watchdog polls the console.

It fails (exit 3) if the console stops answering for 8 s; the server is then
sent SIGUSR1, which makes it print every thread's stack into the server log.
It fails (exit 1) on any client error other than the client library's own
race on a stream it abandoned, and on a server log that records an ASGI
exception or a crashed MCP session. After the load it recycles every
resource and, without forcing a garbage
collection, requires the server to hold no kernel processes and to return to
its starting file descriptors (exit 1 otherwise). Those are the two ways a
long-running server failed before: an event loop that never yielded, and
resources that only a full garbage collection would have released.

File descriptor and process checks read /proc and run on Linux only.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import anyio
import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

CELLS = (
    "print('x' * 10)",
    "print('y' * 200_000)",
    "for i in range(2000): print(i)",
    "%%loommux --wait 0.3\nimport time; time.sleep(1.5); print('slept')",
    # As a client's first cell this names its main kernel: opened, numbered
    # while other sessions are connected, or taken back. Later it forwards to
    # the home's child kernel: the follow endpoint, busy refusals between
    # forwards, and child kernels retired with their home.
    "%%loommux --kernel stress-forward --wait 0.5\nimport time; time.sleep(0.2); print('forwarded')",
    # Moves the session's lease between main kernels under load, or is
    # refused with kernel_not_found when none by that name exists.
    "%%loommux --attach stress-forward\nprint('attached')",
    "1 + 1",
    "raise ValueError('boom')",
)
SERVER_SETTINGS = {
    "LOOMMUX_LEASE_MODE": "connection",
    "LOOMMUX_SWEEP_INTERVAL_SECONDS": "0.2",
    "LOOMMUX_CONNECTION_GRACE_SECONDS": "1",
    "LOOMMUX_NAMED_TTL_SECONDS": "1",
    "LOOMMUX_ORPHAN_GRACE_SECONDS": "1",
}
CONSOLE_STALL_SECONDS = 8
# httpx builds a transport for every proxy variable when a client is created,
# before NO_PROXY is consulted, so a developer's ALL_PROXY=socks5h://... makes
# even a loopback client (FastMCP's transport included) require the optional
# socksio package. The script only talks to the server it starts on loopback.
PROXY_ENVIRONMENT = tuple(
    variant
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
    for variant in (name, name.lower())
)
# After recycling every resource the server is back at its startup count (8 at
# start, 7 after a 60 s run of 380 sessions). Event loops left to the garbage
# collector cost three descriptors per kernel start or shutdown, 863 after a
# 60 s run, so a small allowance separates noise from that failure.
FD_ALLOWANCE = 12
# What uvicorn and the MCP SDK log when a request or a session fails on the
# server's side ("Session <id> crashed", "Stateless session crashed").
SERVER_FAILURE = re.compile(r"Exception in ASGI application|[Ss]ession (\S+ )?crashed")


class Stats:
    def __init__(self) -> None:
        self.calls = 0
        self.errors = 0
        self.abandoned_stream_races = 0
        self.sessions = 0
        self.max_console_seconds = 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=180)
    parser.add_argument("--private", type=int, default=6, help="concurrent private-resource clients")
    parser.add_argument("--shared", type=int, default=4, help="concurrent clients of one shared resource")
    parser.add_argument("--report-dir", type=Path, default=Path("tmp/stress"))
    options = parser.parse_args()
    # The load abandons calls mid-flight on purpose; the MCP client then logs a
    # traceback for every message that reaches a stream it already closed.
    logging.getLogger("mcp.client.streamable_http").setLevel(logging.CRITICAL)
    for name in PROXY_ENVIRONMENT:
        os.environ.pop(name, None)

    options.report_dir.mkdir(parents=True, exist_ok=True)
    log_path = options.report_dir / f"server-{int(time.time())}.log"
    workspace = Path(tempfile.mkdtemp(prefix="loommux-stress-"))
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    with log_path.open("w") as log:
        server = subprocess.Popen(
            [sys.executable, "-m", "loommux.mcp.server", "--server", "--port", str(port)],
            cwd=workspace,
            env={**os.environ, **SERVER_SETTINGS},
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            return _run(server, base, options, log_path)
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    server.kill()


def _run(server: subprocess.Popen[bytes], base: str, options: argparse.Namespace, log_path: Path) -> int:
    _wait_until_ready(base)
    baseline = _descriptors(server.pid)
    stats = Stats()
    stalled = anyio.run(_load, base, options, stats)
    report: dict[str, object] = {
        "calls": stats.calls,
        "errors": stats.errors,
        "abandoned_stream_races": stats.abandoned_stream_races,
        "sessions": stats.sessions,
        "max_console_seconds": round(stats.max_console_seconds, 3),
        "server_log": str(log_path),
    }
    if stalled:
        os.kill(server.pid, signal.SIGUSR1)
        time.sleep(1)
        server.kill()
        report["result"] = f"console did not answer within {CONSOLE_STALL_SECONDS} s; thread stacks are in the server log"
        print(json.dumps(report, indent=2))
        return 3

    httpx.post(f"{base}/api/resources/recycle-all", timeout=60).raise_for_status()
    _wait_for(lambda: not httpx.get(f"{base}/api/resources", timeout=10).json()["resources"], 30)
    if sys.platform == "linux":
        _wait_for(lambda: not _children(server.pid), 15)
        time.sleep(2)
        after = _descriptors(server.pid)
        report |= {"descriptors_at_start": baseline, "descriptors_after_recycle": after, "kernel_processes_after_recycle": len(_children(server.pid))}
        failures = [
            name
            for name, failed in (
                ("kernel processes remain", bool(_children(server.pid))),
                ("file descriptors were not released", after["all"] > baseline["all"] + FD_ALLOWANCE),
                ("event loops were not closed", after["eventpoll"] > baseline["eventpoll"] + FD_ALLOWANCE // 3),
            )
            if failed
        ]
    else:
        failures = []
        report["descriptors"] = "not checked on this platform"
    server_failures = [line for line in log_path.read_text().splitlines() if SERVER_FAILURE.search(line)]
    if server_failures:
        failures.append(f"server logged {len(server_failures)} failures, first: {server_failures[0][:160]}")
    if stats.errors:
        failures.append(f"{stats.errors} client errors")
    report["result"] = "; ".join(failures) or "ok"
    print(json.dumps(report, indent=2))
    return 1 if failures else 0


async def _load(base: str, options: argparse.Namespace, stats: Stats) -> bool:
    deadline = time.monotonic() + options.seconds
    stalled = False

    async def watchdog() -> None:
        nonlocal stalled
        async with httpx.AsyncClient(base_url=base) as http:
            while time.monotonic() < deadline:
                started = time.monotonic()
                try:
                    await http.get("/api/resources", timeout=CONSOLE_STALL_SECONDS)
                    if random.random() < 0.05:
                        await http.post("/api/resources/recycle-idle", timeout=CONSOLE_STALL_SECONDS)
                except httpx.TimeoutException:
                    stalled = True
                    group.cancel_scope.cancel()
                    return
                stats.max_console_seconds = max(stats.max_console_seconds, time.monotonic() - started)
                await anyio.sleep(0.2)

    async with anyio.create_task_group() as group:
        group.start_soon(watchdog)
        for index in range(options.private):
            group.start_soon(_client_loop, base, f"private-{index}", None, deadline, stats)
        for index in range(options.shared):
            group.start_soon(_client_loop, base, f"shared-{index}", "stress-shared", deadline, stats)
    return stalled


async def _client_loop(base: str, name: str, resource: str | None, deadline: float, stats: Stats) -> None:
    headers = {"X-Loommux-Resource": resource} if resource else {}
    while time.monotonic() < deadline:
        try:
            async with Client(StreamableHttpTransport(f"{base}/mcp", headers=headers), timeout=60) as client:
                stats.sessions += 1
                for _ in range(random.randint(1, 12)):
                    # Cancelling at 0.2 s abandons calls mid-flight, which is how
                    # orphaned executions and broken streams arise.
                    with anyio.move_on_after(random.choice((0.2, 2, 30))):
                        await client.call_tool(*_random_call(), raise_on_error=False)
                    stats.calls += 1
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            # The MCP client sometimes trips over a stream it closed itself
            # after a call was abandoned midway; the server log is the
            # authority on server-side failures.
            stats.abandoned_stream_races += 1
        except Exception as exc:
            stats.errors += 1
            print(f"{name}: {type(exc).__name__}: {exc}"[:200], file=sys.stderr, flush=True)
        await anyio.sleep(random.random() * 0.5)


def _random_call() -> tuple[str, dict[str, object]]:
    roll = random.random()
    if roll < 0.55:
        return "run_cell", {"freeform": random.choice(CELLS)}
    if roll < 0.7:
        return "wait", {"timeout_seconds": 1}
    if roll < 0.85:
        return "status", {}
    if roll < 0.93:
        return "interrupt", {}
    return "restart", {}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait_until_ready(base: str) -> None:
    def ready() -> bool:
        try:
            return httpx.get(f"{base}/api/resources", timeout=1).status_code == 200
        except httpx.HTTPError:
            return False

    _wait_for(ready, 60)


def _wait_for(condition: Callable[[], bool], seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError(f"condition not met within {seconds} s")
        time.sleep(0.2)


def _descriptors(pid: int) -> dict[str, int]:
    if sys.platform != "linux":
        return {}
    targets = [os.readlink(entry) for entry in Path(f"/proc/{pid}/fd").iterdir()]
    return {"all": len(targets), "eventpoll": sum(target == "anon_inode:[eventpoll]" for target in targets)}


def _children(pid: int) -> list[int]:
    """Child processes of ``pid``, including zombies not yet reaped."""
    children = []
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = stat.read_text().rsplit(")", 1)[1].split()
        except OSError:
            continue
        if int(fields[1]) == pid:
            children.append(int(stat.parent.name))
    return children


if __name__ == "__main__":
    raise SystemExit(main())
