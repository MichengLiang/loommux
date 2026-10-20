"""Run a real loommux HTTP server in a subprocess for end-to-end tests."""

import asyncio
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx


def reserve_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def running_server(port: int, workspace: Path, **lease_environment: str) -> Iterator[str]:
    origin = f"http://127.0.0.1:{port}"
    environment = {
        **os.environ,
        "LOOMMUX_LEASE_MODE": "heartbeat",
        "LOOMMUX_PRIVATE_TTL_SECONDS": "0.2",
        "LOOMMUX_NAMED_TTL_SECONDS": "0.2",
        "LOOMMUX_HEARTBEAT_INTERVAL_SECONDS": "0.04",
        "LOOMMUX_HEARTBEAT_TIMEOUT_SECONDS": "0.16",
        "LOOMMUX_SWEEP_INTERVAL_SECONDS": "0.02",
        "LOOMMUX_ORPHAN_GRACE_SECONDS": "0.05",
        **lease_environment,
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "loommux.mcp.server",
            "--server",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=workspace,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                raise RuntimeError(f"loommux server exited early:\n{output}")
            try:
                response = httpx.get(
                    f"{origin}/api/lease-policy",
                    timeout=0.2,
                )
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        else:
            raise TimeoutError("loommux server did not start within 10 seconds")
        yield origin
    finally:
        if process.poll() is None:
            if sys.platform == "win32":
                # Windows has no POSIX process groups; terminate the test
                # server directly instead of calling the unavailable killpg.
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if sys.platform == "win32":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)


async def wait_for_resource_count(
    origin: str,
    expected: int,
    *,
    timeout: float = 3,
) -> list[dict]:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(timeout=1) as http:
        while time.monotonic() < deadline:
            resources = (await http.get(f"{origin}/api/resources")).json()[
                "resources"
            ]
            if len(resources) == expected:
                return resources
            await asyncio.sleep(0.03)
    raise AssertionError(f"resource count did not become {expected}")
