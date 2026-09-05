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

from loommux.client import LeaseAwareClient

REPO_ROOT = Path(__file__).resolve().parents[2]


def reserve_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def running_server(port: int, workspace: Path) -> Iterator[str]:
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


def test_http_resources_are_private_shareable_and_lease_reclaimed(
    tmp_path: Path,
) -> None:
    async def scenario(origin: str) -> None:
        server_url = f"{origin}/mcp"

        async with LeaseAwareClient(server_url, "private-a") as first:
            first_result = await first.call_tool(
                "run_cell",
                {"freeform": "private_value = 7\nprint(private_value)"},
            )
            assert first_result.content[0].text == "In [1]:\n7\n"

        async with LeaseAwareClient(server_url, "private-b") as second:
            isolated = await second.call_tool(
                "run_cell",
                {"freeform": "print(globals().get('private_value'))"},
            )
            assert isolated.content[0].text == "In [1]:\nNone\n"

        async with LeaseAwareClient(
            server_url,
            "named-a",
            resource_name="shared-lab",
        ) as first_shared:
            await first_shared.call_tool(
                "run_cell",
                {"freeform": "shared_value = 11"},
            )

        async with LeaseAwareClient(
            server_url,
            "named-b",
            resource_name="shared-lab",
        ) as second_shared:
            shared = await second_shared.call_tool(
                "run_cell",
                {"freeform": "print(shared_value)"},
            )
            assert shared.content[0].text == "In [2]:\n11\n"

        async with LeaseAwareClient(
            server_url,
            "persistent",
            resource_name="heartbeat-lab",
        ) as persistent:
            first = await persistent.call_tool(
                "run_cell",
                {"freeform": "print('first')"},
            )
            await asyncio.sleep(0.35)
            second = await persistent.call_tool(
                "run_cell",
                {"freeform": "print('second')"},
            )
            assert first.content[0].text == "In [1]:\nfirst\n"
            assert second.content[0].text == "In [2]:\nsecond\n"
            assert persistent.heartbeat_count >= 4

        await wait_for_resource_count(origin, 0)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with running_server(reserve_port(), workspace) as origin:
        asyncio.run(scenario(origin))
