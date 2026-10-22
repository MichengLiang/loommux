"""How an MCP session chooses, names, and switches its main kernel, against a real HTTP daemon."""

import asyncio
import sys
from pathlib import Path

import pytest
from server_harness import reserve_port, running_server, wait_for_resource_count

from loommux.client import LeaseAwareClient

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="external Streamable HTTP subprocess harness is Linux-only")


async def texts(client: LeaseAwareClient, source: str) -> list[str]:
    result = await client.call_tool("run_cell", {"freeform": source})
    return [block.text for block in result.content]


@pytest.mark.timeout(90)
def test_a_session_names_its_main_kernel_and_switches_to_another_by_attaching(tmp_path: Path) -> None:
    async def scenario(origin: str) -> None:
        async with LeaseAwareClient(f"{origin}/mcp", "first") as first, LeaseAwareClient(f"{origin}/mcp", "second") as second:
            # B2, B11: the first cell names the main kernel and runs in it.
            assert await texts(first, "%%loommux --kernel task\nvalue = 41\nvalue + 1") == ["In [1]:\nOut[1]: 42\n", "Kernel: task (opened)"]
            # The same header later is the kernel's own, not a forward.
            assert await texts(first, "%%loommux --kernel task\nprint(loommux.self.name)") == ["In [2]:\ntask\n"]
            status = await first.call_tool("status", {})
            assert status.content[0].text.startswith("name: task\nkernel: idle\n")

            # B4: task is connected, so the second session gets task-2, and
            # its first cell runs there instead of being forwarded to a child.
            numbered = await texts(second, "%%loommux --kernel task\nprint(loommux.self.name, globals().get('value'))")
            assert numbered == ["In [1]:\ntask-2 None\n", "Kernel: task-2 (opened; task is in use by another session)"]

            # B6: a missing name opens nothing and allocates no execution.
            missing = await texts(second, "%%loommux --attach gone\n1")
            assert missing == ["kernel_not_found: no main kernel named 'gone'; main kernels no session is connected to: none"]

            # B5: attaching joins task and lets go of task-2.
            attached = await texts(second, "%%loommux --attach task\nprint(value)")
            assert attached == ["In [3]:\n41\n", "Kernel: task (attached; 2 earlier executions; let go of task-2)"]
            assert await texts(second, "print(loommux.self.name)") == ["In [4]:\ntask\n"]
            [resource] = await wait_for_resource_count(origin, 1)
            assert (resource["name"], resource["lease_count"]) == ("task", 2)

            # B7: --attach reaches a kernel only through a forward here. IPython
            # reports a UsageError without failing the execution, as it does
            # for a misplaced header, so the relayed message is the result.
            forwarded = await texts(second, "%%loommux --kernel build\n%%loommux --attach task\nprint('never')")
            assert forwarded[0].startswith("In [5]:\nUsageError: --attach task moves an MCP session to another main kernel")
            assert "never" not in forwarded[0]

        await wait_for_resource_count(origin, 0, timeout=10)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with running_server(reserve_port(), workspace, LOOMMUX_LEASE_MODE="connection", LOOMMUX_CONNECTION_GRACE_SECONDS="0.3") as origin:
        asyncio.run(scenario(origin))
