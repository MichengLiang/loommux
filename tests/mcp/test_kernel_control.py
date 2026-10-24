"""The ``loommux`` object inside a kernel, driving a real HTTP daemon."""

import asyncio
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from server_harness import reserve_port, running_server, wait_for_resource_count

from loommux.client import LeaseAwareClient
from loommux.daemon import Daemon, DaemonError
from loommux.mcp.factory import create_mcp

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="external Streamable HTTP subprocess harness is Linux-only")


async def output(client: LeaseAwareClient | Client, source: str) -> str:
    result = await client.call_tool("run_cell", {"freeform": source})
    return result.content[0].text.partition("\n")[2]


@pytest.mark.timeout(60)
def test_the_header_the_wait_tool_and_a_kernels_methods_share_one_silence(tmp_path: Path) -> None:
    # S5: one meaning of silence on both panels.
    async def scenario(origin: str) -> None:
        async with LeaseAwareClient(f"{origin}/mcp", "agent") as agent:
            first = await agent.call_tool("run_cell", {"freeform": "%%loommux --wait 20 --silence 1\nimport time\nprint('compiled')\ntime.sleep(30)"})
            assert first.content[0].text.startswith("In [1]:\nRunning, silent for 1 s: ")
            assert first.content[0].text.endswith("Last lines:\n  compiled")
            started = asyncio.get_running_loop().time()
            again = await agent.call_tool("wait", {"timeout_seconds": 20, "silence_seconds": 1})
            assert again.content[0].text.startswith("In [1]:\nRunning, silent for 1 s: ")
            assert asyncio.get_running_loop().time() - started >= 0.9
            await agent.call_tool("interrupt", {})
            await agent.call_tool("wait", {"timeout_seconds": 10})

            seen = await output(
                agent,
                "build = loommux.create('build')\n"
                "r = build.run_cell(\"import time\\nprint('a')\\ntime.sleep(30)\", wait_seconds=20, silence_seconds=1)\n"
                "w = build.wait(r['execution'], 20, silence_seconds=1)\n"
                "build.interrupt()\n"
                "print(r['status'], r['silent_seconds'] >= 1, r['recent_lines'], w['status'], w['silent_seconds'] >= 1)",
            )
            assert seen == "running True ['a'] running True\n"

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with running_server(reserve_port(), workspace, lease_mode="connection") as origin:
        asyncio.run(scenario(origin))


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

            # The listing is every kernel, as the console's.
            listing = await output(agent, "print(loommux.kernels())")
            assert listing.splitlines()[0].split() == ["name", "id", "kernel", "busy", "idle", "pid", "executions", "leases", "kept", "scope", "home"]
            assert sorted(row.split()[0] for row in listing.splitlines()[1:]) == sorted([name, "lab"])

            # The person running loommux reaches every main kernel by name.
            owner = Daemon(origin)
            assert {kernel.name for kernel in owner.kernels()} == {name, "lab"}
            lab_handle = owner["lab"]
            assert (lab_handle.status()["name"], lab_handle.status()["kernel_state"]) == ("lab", "running")
            assert lab_handle.restart()["status"] == "restarted"
            assert "NameError" in await output(lab, "kept_in_lab")
            assert owner.recycle(lab_handle, force=True) == "recycled lab"
            owner.close()

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with running_server(reserve_port(), workspace, lease_mode="connection") as origin:
        asyncio.run(scenario(origin))


@pytest.mark.timeout(60)
def test_notices_reach_the_agent_from_its_kernel_and_through_a_forwarded_cell(tmp_path: Path) -> None:
    async def scenario(origin: str) -> None:
        async with LeaseAwareClient(f"{origin}/mcp", "agent") as agent:
            own = await agent.call_tool("run_cell", {"freeform": "loommux.notify('from the main kernel')\n'main'"})
            forwarded = await agent.call_tool("run_cell", {"freeform": "%%loommux --kernel build --wait 30\nloommux.notify('from the child')\n'child'"})
            later = await agent.call_tool("status", {})

        # The notice comes before the Kernel: line the first call also carries, in one envelope.
        assert [block.text for block in own.content][0] == "In [1]:\nOut[1]: 'main'\n"
        assert own.content[1].text.startswith("\n<loommux-notice>\nfrom the main kernel\nKernel: private-")
        # The child's own Out label is its own count; the notice names the child.
        assert [block.text for block in forwarded.content] == ["In [2]:\nOut[1]: 'child'\n", "\n<loommux-notice>\nbuild · from the child\n</loommux-notice>"]
        assert all("from the" not in block.text for block in later.content)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with running_server(reserve_port(), workspace, lease_mode="connection") as origin:
        asyncio.run(scenario(origin))


@pytest.mark.timeout(90)
def test_a_kernel_opens_runs_and_holds_kernels_that_leave_with_it(tmp_path: Path) -> None:
    async def scenario(origin: str) -> None:
        async with LeaseAwareClient(f"{origin}/mcp", "agent") as agent:
            opened = await output(agent, "build = loommux.create('build')\nprint(build.name, build.snapshot['scope'], build.snapshot['home'] == loommux.self.name)")
            assert opened == "build child True\n"

            started = await output(agent, "n = build.run_cell(\"import time\\nprint('a', flush=True)\\ntime.sleep(0.5)\\nprint('b')\\n6 * 7\", wait_seconds=0.05)['execution']\nprint(n, build.execution_status(n)['status'])")
            assert started == "1 running\n"
            finished = await output(
                agent,
                "print(build.wait(n, timeout_seconds=10)['status'])\n"
                "print(build.read_output(n)['text'])\n"
                "print(build.read_output(n, 'stdout', '-1:')['text'])\n"
                "print(build.search_output('b', n, 'stdout')['text'])",
            )
            assert finished == "completed\na\nb\nOut[1]: 42\nb\nM 2 | b\n"

            busy = await output(
                agent,
                "from loommux.daemon import DaemonError\n"
                "long = build.run_cell('import time; time.sleep(1)', wait_seconds=0.05)['execution']\n"
                "try:\n    build.run_cell('1')\nexcept DaemonError as error:\n    print(error.status, error)\n"
                # None selects the current execution, then the most recent, as in the tools.
                "print(build.wait(timeout_seconds=10)['execution'] == long, build.execution_status()['status'])",
            )
            assert busy == "busy kernel is already executing code\nTrue completed\n"

            # A child kernel opening a kernel opens it in the same home, and a
            # sibling's name resolves to the sibling: every kernel here leaves
            # with the agent's main kernel.
            await output(agent, "build.run_cell(\"loommux.create('cache')\")\nloommux['cache'].run_cell(\"loommux['build']\")")
            home = await output(agent, "print(sorted((k.name, k.snapshot['scope'], k.snapshot['home']) for k in loommux.kernels() if k.name != loommux.self.name))")
            agent_name = (await output(agent, "print(loommux.self.name)")).strip()
            assert home == f"[('build', 'child', '{agent_name}'), ('cache', 'child', '{agent_name}')]\n"
            build_id = (await output(agent, "print(build.id)")).strip()

            outside = Daemon(origin)
            assert outside[build_id].name == "build"
            # Without a context a bare name is a main kernel's; a path reaches a child.
            with pytest.raises(DaemonError, match="no kernel 'build'; main kernels: /"):
                outside["build"]
            assert outside[f"/{agent_name}/build"].id == build_id
            with pytest.raises(DaemonError, match="only an MCP session opens"):
                outside.create("fresh")
            outside.close()
            # A closed client opens new connections for its next request.
            assert outside[agent_name].name == agent_name
            drive_from_outside(outside, build_id)

        await wait_for_resource_count(origin, 0, timeout=10)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with running_server(reserve_port(), workspace, lease_mode="connection", connection_grace_seconds=0.3) as origin:
        asyncio.run(scenario(origin))


def drive_from_outside(daemon: Daemon, build_id: str) -> None:
    """The whole client surface, from a process that is not a kernel."""
    assert repr(daemon) == f"loommux daemon {daemon.url}"
    with pytest.raises(DaemonError, match="not a Loommux kernel"):
        _ = daemon.self
    assert repr(daemon.kernels()).splitlines()[0].split()[-2:] == ["scope", "home"]
    build = daemon[build_id]
    assert repr(build) == f"<loommux kernel build {build.id[:8]}>"
    assert build.status()["name"] == "build"

    n = build.run_cell("import time\nprint('outside', flush=True)\ntime.sleep(0.3)\n'done'", wait_seconds=0.05)["execution"]
    followed = build.follow(n, 0, wait=1)
    assert followed["text"] == "outside\n"
    assert build.execution_status(n)["status"] in {"running", "completed"}
    assert build.wait(n, timeout_seconds=10)["status"] == "completed"
    assert build.read_output(n, "result")["text"].endswith("'done'")
    assert build.search_output("OUTSIDE", n, ignore_case=True, context_after=1)["matched_lines"] == 1

    sleeper = build.run_cell("import time; time.sleep(30)", wait_seconds=0.05)["execution"]
    assert build.interrupt()["status"] == "interrupt_sent"
    assert build.wait(sleeper, timeout_seconds=10)["status"] == "interrupted"
    assert build.restart()["status"] == "restarted"
    with pytest.raises(DaemonError, match="execution was not found"):
        build.execution_status(999)

    assert daemon.rename(build, "built") == "built"
    assert build.name == "built"
    assert daemon.recycle(build_id) == "recycled built"


def test_a_daemon_that_cannot_be_reached_says_where_it_looked() -> None:
    unreachable = Daemon(f"http://127.0.0.1:{reserve_port()}")

    with pytest.raises(DaemonError, match="cannot reach the loommux daemon at http://127.0.0.1:"):
        unreachable.kernels()


async def test_a_kernel_of_a_daemon_without_http_reports_that_no_control_plane_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    async with Client(create_mcp("content")) as client:
        named = await output(client, "print(loommux.self.name.startswith('private-'))")
        refused = await output(client, "loommux.kernels()")

    assert named == "True\n"
    assert "DaemonError: this kernel's daemon runs over stdio and serves no HTTP control plane" in refused


@pytest.mark.timeout(120)
def test_a_header_naming_another_kernel_forwards_the_cell_there(tmp_path: Path) -> None:
    async def scenario(origin: str) -> None:
        async with LeaseAwareClient(f"{origin}/mcp", "agent") as agent:
            me = (await output(agent, "print(loommux.self.name)")).strip()

            relayed = await output(agent, "%%loommux --kernel build --wait 30\nimport time\nfor i in range(3):\n    print(i, flush=True)\n    time.sleep(0.2)\n6 * 7")
            assert relayed == "0\n1\n2\nOut[1]: 42\n"
            assert await output(agent, "%%loommux --kernel build --wait 30\n%%bash\necho from-bash") == "from-bash\n"
            assert await output(agent, "%%loommux --kernel build\nimport asyncio\nawait asyncio.sleep(0)\n'awaited'") == "Out[3]: 'awaited'\n"
            assert await output(agent, f"%%loommux --kernel {me} --wait 5\nlocal = 5\nlocal") == "Out[5]: 5\n"

            failed = await agent.call_tool("run_cell", {"freeform": "%%loommux --kernel build --wait 30\nmissing_name"})
            failed_text = failed.content[0].text
            assert "NameError: name 'missing_name' is not defined" in failed_text
            assert failed_text.rstrip().endswith("ForwardedCellError: build: NameError: name 'missing_name' is not defined")

            # build forwards back into the agent, which is busy forwarding to
            # build: refused at once rather than waiting on itself.
            cycle = await output(agent, f"%%loommux --kernel build --wait 30\n%%loommux --kernel {me}\nprint('never')")
            assert "never" not in cycle
            assert cycle.rstrip().endswith(f"ForwardedCellError: build: ForwardedCellError: {me}: busy: kernel is already executing code")

            running = await agent.call_tool("run_cell", {"freeform": "%%loommux --kernel build --wait 0.5\nimport time\nprint('started', flush=True)\ntime.sleep(30)"})
            assert "started" in running.content[0].text or "Running" in running.content[0].text
            for _ in range(50):
                seen = await agent.call_tool("read_output", {})
                if "started" in seen.content[0].text:
                    break
                await asyncio.sleep(0.1)
            await agent.call_tool("interrupt", {})
            interrupted = await agent.call_tool("wait", {"timeout_seconds": 20})
            assert interrupted.content[0].text.rstrip().endswith("KeyboardInterrupt: passed on to build; its traceback is above")
            status = await agent.call_tool("execution_status", {})
            assert "interrupted" in status.content[0].text
            assert await output(agent, "print(loommux['build'].status()['busy'])") == "False\n"

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with running_server(reserve_port(), workspace, lease_mode="connection") as origin:
        asyncio.run(scenario(origin))
