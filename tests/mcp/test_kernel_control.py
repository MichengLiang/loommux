"""The ``loommux`` object inside a kernel, driving a real HTTP daemon."""

import asyncio
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from server_harness import reserve_port, running_server

from loommux.client import LeaseAwareClient
from loommux.mcp.factory import create_mcp

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="external Streamable HTTP subprocess harness is Linux-only")


async def output(client: LeaseAwareClient | Client, source: str) -> str:
    result = await client.call_tool("run_cell", {"freeform": source})
    return result.content[0].text.partition("\n")[2]


@pytest.mark.timeout(60)
def test_a_kernel_knows_itself_and_manages_other_kernels_through_its_daemon(tmp_path: Path) -> None:
    async def scenario(origin: str) -> None:
        async with (
            LeaseAwareClient(f"{origin}/mcp", "agent") as agent,
            LeaseAwareClient(f"{origin}/mcp", "lab-user", resource_name="lab") as lab,
        ):
            await output(lab, "kept_in_lab = 1")
            identity = await output(agent, "import os\nprint(loommux.self.name, loommux.self.id == os.environ['LOOMMUX_KERNEL_ID'])\nprint(loommux)")
            name = identity.split()[0]
            assert name.startswith("private-")
            assert f"True\nloommux daemon {origin} · this kernel {name}" in identity

            listing = await output(agent, "print(loommux.kernels())")
            assert listing.splitlines()[0].split() == ["name", "id", "kernel", "busy", "pid", "executions", "leases"]
            assert {row.split()[0] for row in listing.splitlines()[1:]} == {name, "lab"}

            lab_status = await output(agent, "lab = loommux['lab']\nprint(lab.status()['name'], lab.status()['kernel_state'])")
            assert lab_status == "lab running\n"
            assert "restarted" in await output(agent, "print(lab.restart()['status'])")
            assert "NameError" in await output(lab, "kept_in_lab")

            missing = await output(agent, "loommux['nowhere']")
            assert "KeyError: \"no kernel named 'nowhere'\"" in missing

            assert await output(agent, "print(lab.recycle(force=True))") == "recycled lab\n"
            assert await output(agent, "print([k.name for k in loommux.kernels()] == [loommux.self.name])") == "True\n"

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with running_server(reserve_port(), workspace, LOOMMUX_LEASE_MODE="connection") as origin:
        asyncio.run(scenario(origin))


async def test_a_kernel_of_a_daemon_without_http_reports_that_no_control_plane_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    async with Client(create_mcp("content")) as client:
        named = await output(client, "print(loommux.self.name.startswith('private-'))")
        refused = await output(client, "loommux.kernels()")

    assert named == "True\n"
    assert "DaemonError: this kernel's daemon runs over stdio and serves no HTTP control plane" in refused
