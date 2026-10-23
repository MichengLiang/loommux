"""Run a real loommux HTTP server in a subprocess for end-to-end tests."""

import asyncio
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx

# Short windows so lease expiry and sweeps happen within a test's timeout.
HARNESS_SETTINGS: dict[str, object] = {
    "lease_mode": "heartbeat",
    "private_activity_timeout_seconds": 0.2,
    "named_activity_timeout_seconds": 0.2,
    "heartbeat_interval_seconds": 0.04,
    "heartbeat_timeout_seconds": 0.16,
    "sweep_interval_seconds": 0.02,
    "orphan_grace_seconds": 0.05,
}


def reserve_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def running_server(port: int, workspace: Path, **settings: object) -> Iterator[str]:
    """A server started with a configuration directory whose config.py sets ``settings`` over the harness defaults."""
    with tempfile.TemporaryDirectory(prefix="loommux-config-") as config:
        source = "".join(f"{name} = {value!r}\n" for name, value in {**HARNESS_SETTINGS, **settings}.items())
        Path(config, "config.py").write_text(source, encoding="utf-8")
        with _server(port, workspace, config) as origin:
            yield origin


@contextmanager
def _server(port: int, workspace: Path, config: str) -> Iterator[str]:
    origin = f"http://127.0.0.1:{port}"
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
            "--config",
            config,
        ],
        cwd=workspace,
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
