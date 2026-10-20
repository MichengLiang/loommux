"""The ``loommux`` object inside a kernel, driving a real HTTP daemon."""

import asyncio
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from server_harness import reserve_port, running_server, wait_for_resource_count

from loommux.client import LeaseAwareClient
from loommux.daemon import Daemon, DaemonError, Job
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
            assert listing.splitlines()[0].split() == ["name", "id", "kernel", "busy", "pid", "executions", "leases", "holders"]
            assert {row.split()[0] for row in listing.splitlines()[1:]} == {name, "lab"}

            lab_status = await output(agent, "lab = loommux['lab']\nprint(lab.status()['name'], lab.status()['kernel_state'])")
            assert lab_status == "lab running\n"
            assert "restarted" in await output(agent, "print(lab.restart()['status'])")
            assert "NameError" in await output(lab, "kept_in_lab")

            assert await output(agent, "print(lab.recycle(force=True))") == "recycled lab\n"
            assert await output(agent, "print([k.name for k in loommux.kernels()] == [loommux.self.name])") == "True\n"

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with running_server(reserve_port(), workspace, LOOMMUX_LEASE_MODE="connection") as origin:
        asyncio.run(scenario(origin))


@pytest.mark.timeout(90)
def test_a_kernel_opens_runs_and_holds_kernels_that_leave_with_it(tmp_path: Path) -> None:
    async def scenario(origin: str) -> None:
        async with LeaseAwareClient(f"{origin}/mcp", "agent") as agent:
            opened = await output(agent, "build = loommux['build']\nprint(build.name, build.snapshot['held_by'] == [loommux.self.name])")
            assert opened == "build True\n"

            started = await output(agent, "job = build.run(\"import time\\nprint('a', flush=True)\\ntime.sleep(0.5)\\nprint('b')\\n6 * 7\", wait=0.05)\nprint(job.done, job)")
            assert started == "False <loommux job build #1 running>\n"
            finished = await output(agent, "print(job.wait(10)['status'], job.done)\nprint(job.output())\nprint(job.output('stdout', '-1:'))")
            assert finished == "completed True\na\nb\nOut[1]: 42\nb\n"

            busy = await output(
                agent,
                "from loommux.daemon import DaemonError\n"
                "long = build.run('import time; time.sleep(1)', wait=0.05)\n"
                "try:\n    build.run('1')\nexcept DaemonError as error:\n    print(error.status, error)\n"
                "print(long.wait(10)['status'], long.status()['status'])",
            )
            assert busy == "busy kernel is already executing code\ncompleted completed\n"

            # The two named kernels hold each other (and the agent holds both,
            # having opened each); only the agent's lease reaches them, so
            # they must leave with it.
            await output(agent, "build.run(\"loommux['cache']\")\nloommux['cache'].run(\"loommux['build']\")")
            cycle = await output(agent, "print(sorted((k.name, tuple(k.snapshot['held_by'])) for k in loommux.kernels() if k.name != loommux.self.name))")
            agent_name = (await output(agent, "print(loommux.self.name)")).strip()
            assert cycle == f"[('build', ('cache', '{agent_name}')), ('cache', ('build', '{agent_name}'))]\n"

            outside = Daemon(origin)
            assert outside["build"].name == "build"
            with pytest.raises(DaemonError, match="only a kernel can open one"):
                outside["elsewhere"]
            drive_from_outside(outside)

        await wait_for_resource_count(origin, 0, timeout=10)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with running_server(reserve_port(), workspace, LOOMMUX_LEASE_MODE="connection", LOOMMUX_CONNECTION_GRACE_SECONDS="0.3") as origin:
        asyncio.run(scenario(origin))


def drive_from_outside(daemon: Daemon) -> None:
    """The whole client surface, from a process that is not a kernel."""
    assert repr(daemon) == f"loommux daemon {daemon.url}"
    with pytest.raises(DaemonError, match="not a Loommux kernel"):
        _ = daemon.self
    assert repr(daemon.kernels()).splitlines()[0].split()[-1] == "holders"
    build = daemon["build"]
    assert repr(build) == f"<loommux kernel build {build.id[:8]}>"
    assert build.status()["name"] == "build"

    job = build.run("import time\nprint('outside', flush=True)\ntime.sleep(0.3)\n'done'", wait=0.05)
    assert repr(job).startswith("<loommux job build #")
    followed = job.follow(0, wait=1)
    assert followed["text"] == "outside\n"
    assert job.status()["status"] in {"running", "completed"}
    assert job.wait(10)["status"] == "completed"
    assert job.done
    assert job.output("result").endswith("'done'")

    sleeper = build.run("import time; time.sleep(30)", wait=0.05)
    assert sleeper.interrupt()["status"] == "interrupt_sent"
    assert sleeper.wait(10)["status"] == "interrupted"
    assert build.restart()["status"] == "restarted"
    with pytest.raises(DaemonError, match="execution was not found"):
        Job(build, 999, {}).status()


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

            relayed = await output(agent, "%%loommux build --wait 30\nimport time\nfor i in range(3):\n    print(i, flush=True)\n    time.sleep(0.2)\n6 * 7")
            assert relayed == "0\n1\n2\nOut[1]: 42\n"
            assert await output(agent, "%%loommux build --wait 30\n%%bash\necho from-bash") == "from-bash\n"
            assert await output(agent, "%%loommux build\nimport asyncio\nawait asyncio.sleep(0)\n'awaited'") == "Out[3]: 'awaited'\n"
            assert await output(agent, f"%%loommux {me} --wait 5\nlocal = 5\nlocal") == "Out[5]: 5\n"

            failed = await agent.call_tool("run_cell", {"freeform": "%%loommux build --wait 30\nmissing_name"})
            failed_text = failed.content[0].text
            assert "NameError: name 'missing_name' is not defined" in failed_text
            assert failed_text.rstrip().endswith("ForwardedCellError: build: NameError: name 'missing_name' is not defined")

            # build forwards back into the agent, which is busy forwarding to
            # build: refused at once rather than waiting on itself.
            cycle = await output(agent, f"%%loommux build --wait 30\n%%loommux {me}\nprint('never')")
            assert "never" not in cycle
            assert cycle.rstrip().endswith(f"ForwardedCellError: build: ForwardedCellError: {me}: busy: kernel is already executing code")

            running = await agent.call_tool("run_cell", {"freeform": "%%loommux build --wait 0.5\nimport time\nprint('started', flush=True)\ntime.sleep(30)"})
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
    with running_server(reserve_port(), workspace, LOOMMUX_LEASE_MODE="connection") as origin:
        asyncio.run(scenario(origin))
