"""How an MCP session chooses, names, and switches its main kernel, against a real HTTP daemon."""

import asyncio
import re
import sys
from pathlib import Path

import httpx
import pytest
from result_text import untimed
from server_harness import reserve_port, running_server, wait_for_resource_count

from loommux.client import LeaseAwareClient

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="external Streamable HTTP subprocess harness is Linux-only")


async def texts(client: LeaseAwareClient, source: str) -> list[str]:
    result = await client.call_tool("run_cell", {"freeform": source})
    return [untimed(block.text) for block in result.content]


def envelope(*lines: str) -> str:
    """What the environment says in one result: one block that starts a line and is closed."""
    return "\n<loommux-notice>\n" + "\n".join(lines) + "\n</loommux-notice>"


@pytest.mark.timeout(90)
def test_a_session_names_its_main_kernel_and_switches_to_another_by_attaching(tmp_path: Path) -> None:
    async def scenario(origin: str) -> None:
        async with LeaseAwareClient(f"{origin}/mcp", "first") as first, LeaseAwareClient(f"{origin}/mcp", "second") as second:
            # B2, B11: the first cell names the main kernel and runs in it.
            assert await texts(first, "%%loommux --attach task --create\nvalue = 41\nvalue + 1") == ["In [1]:\nOut[1]: 42\n", envelope("Kernel: task (opened)")]
            # Repeating the header changes nothing.
            assert await texts(first, "%%loommux --attach task --create\nprint(loommux.self.name)") == ["In [2]:\ntask\n"]
            status = await first.call_tool("status", {})
            assert status.content[0].text.startswith("name: task\nkernel: idle\n")

            # B5: --kernel never chooses the main kernel, on a first call
            # either: it forwards from the session's new private kernel.
            forwarded_first = await texts(second, "%%loommux --kernel build\nprint(loommux.self.name)")
            assert forwarded_first[0] == "In [1]:\nbuild\n"
            opened = re.fullmatch(r"\n<loommux-notice>\nKernel: (.+) \(opened\)\n</loommux-notice>", forwarded_first[1])
            assert opened is not None
            private = opened.group(1)
            assert private.startswith("private-")

            # B4: a missing name opens nothing and allocates no execution.
            missing = await texts(second, "%%loommux --attach gone\n1")
            assert missing == [f"kernel_not_found: no main kernel named 'gone'; main kernels: {private}, task. Add --create to open it"]

            # B3: attaching joins task while first is connected to it, and
            # lets go of the private kernel.
            attached = await texts(second, "%%loommux --attach task\nprint(value)")
            assert attached == ["In [3]:\n41\n", envelope(f"Kernel: task (attached; 2 earlier executions; let go of {private})")]
            assert await texts(second, "print(loommux.self.name)") == ["In [4]:\ntask\n"]
            [resource] = await wait_for_resource_count(origin, 1)
            assert (resource["name"], resource["lease_count"]) == ("task", 2)

            # B6: --attach reaches a kernel only through a forward here. IPython
            # reports a UsageError without failing the execution, as it does
            # for a misplaced header, so the relayed message is the result.
            forwarded = await texts(second, "%%loommux --kernel build\n%%loommux --attach task\nprint('never')")
            assert forwarded[0].startswith("In [5]:\nUsageError: --attach task moves an MCP session to another main kernel")
            assert "never" not in forwarded[0]

        await wait_for_resource_count(origin, 0, timeout=10)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with running_server(reserve_port(), workspace, lease_mode="connection", connection_grace_seconds=0.3) as origin:
        asyncio.run(scenario(origin))


@pytest.mark.timeout(60)
def test_a_main_kernel_whose_session_left_keeps_itself_until_a_new_host_attaches_by_name(tmp_path: Path) -> None:
    async def scenario(origin: str) -> None:
        async with LeaseAwareClient(f"{origin}/mcp", "first") as first:
            assert await texts(first, "%%loommux --attach task --create\nkept = 'still here'") == ["In [1]:", envelope("Kernel: task (opened)")]
            # A throwaway host that never names its kernel leaves beside it.
            async with LeaseAwareClient(f"{origin}/mcp", "throwaway") as throwaway:
                assert (await texts(throwaway, "1"))[1].endswith(" (opened)\n</loommux-notice>")
        # Past the reconnect grace both sessions have left: B8, the lease is
        # released without an alert and the named kernel keeps its own
        # retention; B13, the unnamed one is gone.
        await asyncio.sleep(0.8)
        [resource] = await wait_for_resource_count(origin, 1)
        assert (resource["name"], resource["lease_count"]) == ("task", 0)
        assert 0 < resource["retention_remaining_seconds"] <= 60

        # A new host writes the same first cell and finds the kernel by name.
        async with LeaseAwareClient(f"{origin}/mcp", "second") as second:
            assert await texts(second, "%%loommux --attach task --create\nprint(kept)") == ["In [2]:\nstill here\n", envelope("Kernel: task (attached; 1 earlier execution)")]

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with running_server(reserve_port(), workspace, lease_mode="connection", connection_grace_seconds=0.3, named_activity_timeout_seconds=60) as origin:
        asyncio.run(scenario(origin))


@pytest.mark.timeout(60)
def test_a_kernel_renames_itself_in_one_line_and_learns_a_name_given_elsewhere(tmp_path: Path) -> None:
    async def scenario(origin: str) -> None:
        async with LeaseAwareClient(f"{origin}/mcp", "agent") as agent, LeaseAwareClient(f"{origin}/mcp", "other") as other:
            await texts(other, "%%loommux --attach taken --create\n1")
            # B10: one line inside a cell that does other work.
            renamed = await texts(agent, "work = 6 * 7\nprint(loommux.rename(loommux.self, 'report'), loommux.self.name)")
            assert renamed[0] == "In [1]:\nreport report\n"
            assert await texts(agent, "%%loommux --kernel report\nwork") == ["In [2]:\nOut[2]: 42\n"]
            status = await agent.call_tool("status", {})
            assert status.content[0].text.startswith("name: report\n")
            assert await texts(agent, "print(loommux.rename(loommux.self, 'taken'))") == ["In [3]:\ntaken-2\n"]

            # Renamed from its child kernel, the main kernel learns the name the
            # first time a header uses it, and reads it as its own after.
            await texts(agent, "%%loommux --kernel build\nloommux.rename('taken-2', 'from-child')")
            relearned = await texts(agent, "%%loommux --kernel from-child\nprint('never')")
            assert relearned[0].startswith("In [5]:\nUsageError: from-child is this kernel, renamed from elsewhere")
            assert await texts(agent, "%%loommux --kernel from-child\nprint(loommux.self.name)") == ["In [6]:\nfrom-child\n"]

            async with httpx.AsyncClient(base_url=origin, timeout=5) as http:
                assert (await http.post("/api/resources/missing/rename", json={"name": "x"})).status_code == 404
                assert (await http.post("/api/resources/missing/rename", json={"name": 1})).status_code == 400
                [main] = [r for r in (await http.get("/api/resources")).json()["resources"] if r["name"] == "from-child"]
                assert (await http.post(f"/api/resources/{main['resource_id']}/rename", json={"name": "bad\tname"})).status_code == 400

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with running_server(reserve_port(), workspace, lease_mode="connection", connection_grace_seconds=0.3) as origin:
        asyncio.run(scenario(origin))
