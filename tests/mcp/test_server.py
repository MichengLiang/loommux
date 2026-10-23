from __future__ import annotations

import asyncio
import sys
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from loommux.cell_header import CellHeaderError, read_cell_header
from loommux.kernel.session import KernelSession
from loommux.mcp.factory import create_mcp as create_policy_mcp
from loommux.mcp.result import make_tool_result
from loommux.mcp.server import create_mcp as create_default_mcp
from loommux.session import IPythonSession

WORKSPACE_CONFIG_ENV = "LOOMMUX_WORKSPACE_CONFIG"
TOKEN_LIGHT_MANY_LINES = "\n".join(f"line-{number}" for number in range(301)) + "\n"
TOKEN_HEAVY_LINE = "abcdefghij " * 20
TOKEN_HEAVY_MANY_LINES = f"{TOKEN_HEAVY_LINE}\n" * 301


def create_structured_mcp() -> Any:
    """Use the explicit opt-in policy when testing the structured protocol surface."""
    return create_policy_mcp("structured")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    value = tmp_path / "workspace"
    value.mkdir()
    return value


@pytest.fixture
async def default_client(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Client[Any]]:
    monkeypatch.chdir(workspace)
    async with Client(create_default_mcp()) as client:
        yield client


async def test_result_modes_have_identical_eight_tool_schemas_and_descriptions(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(workspace)
    async with Client(create_structured_mcp()) as structured, Client(create_default_mcp()) as default:
        structured_tools = {tool.name: tool for tool in await structured.list_tools()}
        default_tools = {tool.name: tool for tool in await default.list_tools()}

    expected = {"run_cell", "status", "execution_status", "read_output", "search_output", "wait", "interrupt", "restart"}
    assert set(structured_tools) == set(default_tools) == expected
    for name in expected:
        assert structured_tools[name].inputSchema == default_tools[name].inputSchema
        assert structured_tools[name].description == default_tools[name].description
    schema = structured_tools["read_output"].inputSchema["properties"]
    assert set(schema) == {"execution", "stream", "line_range", "max_chars"}
    assert schema["execution"]["anyOf"][0]["type"] == "integer"


async def test_default_workspace_ignores_legacy_files_and_exposes_launch_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    launch_cwd = project / "nested" / "launch"
    marker = tmp_path / "legacy-executed"
    launch_cwd.mkdir(parents=True)
    (project / "loommux_workspace.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n", encoding="utf-8")
    (launch_cwd / "loommux_workspace.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n", encoding="utf-8")
    monkeypatch.chdir(launch_cwd)

    async with Client(create_default_mcp()) as client:
        status = await client.call_tool("status", {})
        cwd = await client.call_tool("run_cell", {"freeform": "import os\nprint(os.getcwd())"})

    assert status.data is None
    assert f"workspace: {launch_cwd}" in status.content[0].text
    assert "workspace_resolution: launch_cwd" in status.content[0].text
    assert cwd.content[0].text == f"In [1]:\n{launch_cwd}\n"
    assert not marker.exists()


async def test_explicit_resolver_controls_server_workspace_and_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launch_cwd = tmp_path / "launch"
    workspace = tmp_path / "workspace"
    resolver = tmp_path / "resolver.py"
    launch_cwd.mkdir()
    workspace.mkdir()
    resolver.write_text("def resolve_workspace(launch_cwd):\n    return launch_cwd.parent / 'workspace'\n", encoding="utf-8")
    monkeypatch.chdir(launch_cwd)
    monkeypatch.setenv(WORKSPACE_CONFIG_ENV, str(resolver))

    async with Client(create_default_mcp()) as client:
        status = await client.call_tool("status", {})
        cwd = await client.call_tool("run_cell", {"freeform": "import os\nprint(os.getcwd())"})

    assert status.data is None
    assert f"workspace: {workspace}" in status.content[0].text
    assert "workspace_resolution: explicit_config" in status.content[0].text
    assert cwd.content[0].text == f"In [1]:\n{workspace}\n"


async def test_configured_resolution_errors_prevent_server_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launch_cwd = tmp_path / "launch"
    launch_cwd.mkdir()
    resolver_directory = tmp_path / "resolver-directory"
    resolver_directory.mkdir()
    (launch_cwd / "workspace-file").write_text("not a directory", encoding="utf-8")
    cases = [
        ("relative.py", "workspace_config_not_absolute"),
        (str(tmp_path / "missing.py"), "workspace_config_not_found"),
        (str(resolver_directory), "workspace_config_not_file"),
    ]
    for filename, source, status in [
        ("load-error.py", "raise RuntimeError('private resolver source')\n", "workspace_config_load_failed"),
        ("missing-resolver.py", "PRIVATE_RESOLVER_CONTENT = 'private resolver source'\n", "workspace_config_load_failed"),
        ("invalid-return.py", "def resolve_workspace(launch_cwd):\n    return 3\n", "workspace_config_invalid_return"),
        ("missing-workspace.py", "def resolve_workspace(launch_cwd):\n    return 'missing'\n", "workspace_not_found"),
        ("file-workspace.py", "def resolve_workspace(launch_cwd):\n    return 'workspace-file'\n", "workspace_not_directory"),
    ]:
        resolver = tmp_path / filename
        resolver.write_text(source, encoding="utf-8")
        cases.append((str(resolver), status))
    monkeypatch.chdir(launch_cwd)

    for configured_path, expected_status in cases:
        monkeypatch.setenv(WORKSPACE_CONFIG_ENV, configured_path)
        with pytest.raises(RuntimeError, match=expected_status) as error:
            async with Client(create_default_mcp()):
                pytest.fail("MCP tools must not become usable after resolver failure")
        assert "private resolver source" not in str(error.value)


async def test_result_modes_use_the_server_interpreter_and_preserve_workspace_resolution(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(workspace)
    expected_python = str(Path(sys.executable).absolute())
    async with Client(create_structured_mcp()) as structured, Client(create_default_mcp()) as default:
        structured_status = await structured.call_tool("status", {})
        default_status = await default.call_tool("status", {})
        structured_python = await structured.call_tool("run_cell", {"freeform": "import sys\nprint(sys.executable)"})
        default_python = await default.call_tool("run_cell", {"freeform": "import sys\nprint(sys.executable)"})
        reset = await structured.call_tool("restart", {})

    assert structured_status.data["python"] == expected_python
    assert structured_status.data["workspace_resolution"] == "launch_cwd"
    assert "workspace_resolution: launch_cwd" in default_status.content[0].text
    assert expected_python in structured_python.content[0].text
    assert expected_python in default_python.content[0].text
    assert reset.data["workspace_resolution"] == "launch_cwd"


async def test_every_cell_header_example_in_the_tool_descriptions_parses(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The descriptions teach the header by example, and an agent copies them.
    # Their wording is their own authority and is not asserted here; whether an
    # example is a header the real parser accepts is the one thing a machine
    # can check, so the grammar and the examples that teach it cannot drift.
    monkeypatch.chdir(workspace)
    async with Client(create_default_mcp()) as client:
        tools = await client.list_tools()

    examples = [(tool.name, line.strip()) for tool in tools for line in (tool.description or "").splitlines() if line.strip().startswith("%%loommux")]
    rejected = []
    for name, example in examples:
        try:
            read_cell_header(example)
        except CellHeaderError as exc:
            rejected.append((name, example, str(exc)))

    assert examples
    assert rejected == []


async def test_result_modes_share_content_but_only_structured_exposes_structured_status(default_client: Client[Any]) -> None:
    async with Client(create_structured_mcp()) as structured_client:
        structured = await structured_client.call_tool("run_cell", {"freeform": "value = 1"})
    default = await default_client.call_tool("run_cell", {"freeform": "value = 1"})

    assert structured.structured_content is not None
    assert structured.structured_content["execution"] == 1
    assert default.structured_content is None
    assert structured.content[0].text == default.content[0].text == "In [1]:"


async def test_standard_ping_renews_an_existing_resource_lease(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(workspace)
    async with Client(create_structured_mcp()) as client:
        await client.call_tool("run_cell", {"freeform": "value = 1"})
        assert await client.ping()


async def test_mcp_projects_input_coordinate_display_result_and_traceback(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(workspace)
    async with Client(create_structured_mcp()) as structured_client, Client(create_default_mcp()) as default_client:
        structured_silent = await structured_client.call_tool("run_cell", {"freeform": "value = 1"})
        default_silent = await default_client.call_tool("run_cell", {"freeform": "value = 1"})
        structured_result = await structured_client.call_tool("run_cell", {"freeform": "value"})
        default_result = await default_client.call_tool("run_cell", {"freeform": "value"})
        structured_error = await structured_client.call_tool("run_cell", {"freeform": "1 / 0"})
        default_error = await default_client.call_tool("run_cell", {"freeform": "1 / 0"})

    assert structured_silent.content[0].text == default_silent.content[0].text == "In [1]:"
    assert structured_result.content[0].text == default_result.content[0].text == "In [2]:\nOut[2]: 1\n"
    assert structured_error.content[0].text.startswith("In [3]:\n")
    assert default_error.content[0].text.startswith("In [3]:\n")
    assert "ZeroDivisionError" in structured_error.content[0].text
    assert "ZeroDivisionError" in default_error.content[0].text


async def test_mcp_projects_real_display_images_in_iopub_order_with_detail(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(workspace)
    source = """import base64
from IPython.display import Image, display
png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL9ZwAAAABJRU5ErkJggg==')
print('before-image', flush=True)
display(Image(data=png, format='png'), metadata={'detail': 'low'})
print('between-images', flush=True)
display(Image(data=png, format='png'), metadata={'detail': 'original'})
print('after-image', flush=True)
"""
    async with Client(create_structured_mcp()) as client:
        response = await client.call_tool("run_cell", {"freeform": source})

    # The session's first call ends with the block naming its main kernel,
    # after the rich content as after plain text.
    assert [block.type for block in response.content] == ["text", "text", "text", "image", "text", "text", "image", "text", "text"]
    texts = [block.text for block in response.content if block.type == "text"]
    assert texts[:-1] == [
        "In [1]:\n",
        "before-image\n",
        "<IPython.core.display.Image object>",
        "between-images\n",
        "<IPython.core.display.Image object>",
        "after-image\n",
    ]
    assert texts[-1].startswith("Kernel: private-") and texts[-1].endswith(" (opened)")
    images = [block for block in response.content if block.type == "image"]
    assert [image.mimeType for image in images] == ["image/png", "image/png"]
    assert [image.meta["detail"] for image in images] == ["low", "original"]
    assert response.structured_content is not None
    assert "iVBOR" not in str(response.structured_content)


async def test_result_modes_share_marked_complete_long_output(default_client: Client[Any]) -> None:
    source = "%%loommux --full-output\nprint(('abcdefghij ' * 20 + '\\n') * 301, end='')"
    async with Client(create_structured_mcp()) as structured_client:
        structured = await structured_client.call_tool("run_cell", {"freeform": source})
    default = await default_client.call_tool("run_cell", {"freeform": source})

    expected = f"In [1]:\n{TOKEN_HEAVY_MANY_LINES}"
    assert structured.content[0].text == default.content[0].text == expected
    assert structured.structured_content is not None
    assert structured.structured_content["output_omitted"] is False
    assert default.structured_content is None


async def test_token_light_many_line_result_is_delivered_under_the_token_limit(default_client: Client[Any]) -> None:
    source = "print('\\n'.join(f'line-{number}' for number in range(301)))"
    async with Client(create_structured_mcp()) as structured_client:
        structured = await structured_client.call_tool("run_cell", {"freeform": source})
    default = await default_client.call_tool("run_cell", {"freeform": source})

    expected = f"In [1]:\n{TOKEN_LIGHT_MANY_LINES}"
    assert structured.content[0].text == default.content[0].text == expected
    assert structured.structured_content is not None
    assert structured.structured_content["output_omitted"] is False
    assert {"output_total_tokens", "output_token_limit", "output_token_encoding"}.isdisjoint(structured.structured_content)


async def test_token_limited_result_reports_one_human_readable_size_and_exact_structured_metrics(default_client: Client[Any]) -> None:
    source = "print(('abcdefghij ' * 20 + '\\n') * 301, end='')"
    async with Client(create_structured_mcp()) as structured_client:
        structured = await structured_client.call_tool("run_cell", {"freeform": source})
    default = await default_client.call_tool("run_cell", {"freeform": source})

    assert structured.content[0].text == default.content[0].text
    notice = structured.content[0].text
    assert notice.startswith("In [1]:\nOutput omitted: 301 lines")
    assert f"{len(TOKEN_HEAVY_MANY_LINES):,} characters" in notice
    assert "64.96 KiB" in notice
    assert "read_output()" in notice
    assert "search_output()" in notice
    assert "%%loommux --full-output" in notice
    assert structured.structured_content is not None
    assert structured.structured_content["output_total_lines"] == 301
    assert structured.structured_content["output_total_characters"] == len(TOKEN_HEAVY_MANY_LINES)
    assert structured.structured_content["output_total_utf8_bytes"] == len(TOKEN_HEAVY_MANY_LINES.encode("utf-8"))
    assert {"output_total_tokens", "output_token_limit", "output_token_encoding"}.isdisjoint(structured.structured_content)


async def test_shared_factory_binds_every_tool_to_the_integer_contract(default_client: Client[Any]) -> None:
    await default_client.call_tool("status", {})
    submitted = await default_client.call_tool("run_cell", {"freeform": "print('factory')"})
    execution = submitted.data
    # Content results intentionally have no data; the current-or-recent default still
    # exercises the same single selection path without any hidden alternate id.
    status = await default_client.call_tool("execution_status", {})
    read = await default_client.call_tool("read_output", {"stream": "stdout"})
    search = await default_client.call_tool("search_output", {"query": "factory", "stream": "stdout", "query_mode": "literal"})
    wait = await default_client.call_tool("wait", {"timeout_seconds": 1})
    interrupt = await default_client.call_tool("interrupt", {})
    reset = await default_client.call_tool("restart", {})

    assert execution is None
    assert "execution 1: completed" in status.content[0].text
    assert read.content[0].text == "factory"
    assert "M 1 | factory" in search.content[0].text
    assert wait.content[0].text == "In [1]:\nfactory\n"
    assert interrupt.content[0].text == "Python kernel is idle."
    assert "next cell is In [1]" in reset.content[0].text


async def test_real_mcp_eight_tool_loop_starts_the_count_again_after_reset(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(workspace)
    async with Client(create_structured_mcp()) as client:
        initial = await client.call_tool("status", {})
        small = await client.call_tool("run_cell", {"freeform": "print('small-output')"})
        running = await client.call_tool(
            "run_cell",
            {"freeform": "%%loommux --wait 0.1\nimport time\nprint('long-start', flush=True)\ntime.sleep(1.5)\nprint('long-finished', flush=True)"},
        )
        long_execution = running.data["execution"]
        observed = await client.call_tool("execution_status", {"execution": long_execution})
        partial = await client.call_tool("read_output", {"execution": long_execution, "stream": "stdout"})
        found = await client.call_tool("search_output", {"execution": long_execution, "stream": "stdout", "query": "long-start", "query_mode": "literal"})
        completed = await client.call_tool("wait", {"execution": long_execution, "timeout_seconds": 3})
        interruptible = await client.call_tool(
            "run_cell",
            {"freeform": "%%loommux --wait 0.1\nimport time\nprint('interrupt-ready', flush=True)\ntime.sleep(5)"},
        )
        interrupted_execution = interruptible.data["execution"]
        interrupt_ready = await client.call_tool("read_output", {"execution": interrupted_execution, "stream": "stdout"})
        interrupt = await client.call_tool("interrupt", {})
        interrupted = await client.call_tool("wait", {"execution": interrupted_execution, "timeout_seconds": 3})
        reset = await client.call_tool("restart", {})
        old_record = await client.call_tool("read_output", {"execution": small.data["execution"], "stream": "stdout"})
        after_reset = await client.call_tool("run_cell", {"freeform": "print('after-reset')"})

    assert initial.data["workspace"] == str(workspace)
    assert initial.data["workspace_resolution"] == "launch_cwd"
    assert small.data["execution"] == 1
    assert long_execution == 2
    assert running.data["status"] == "running"
    assert observed.data["status"] == "running"
    assert partial.content[0].text == "long-start"
    assert "M 1 | long-start" in found.content[0].text
    assert completed.data["status"] == "completed"
    assert "long-finished" in completed.content[0].text
    assert interrupted_execution == 3
    assert interrupt_ready.content[0].text == "interrupt-ready"
    assert interrupt.data["status"] == "interrupt_sent"
    assert interrupt.data["execution"] == interrupted_execution
    assert interrupted.data["status"] == "interrupted"
    assert reset.data["status"] == "restarted"
    assert old_record.data["status"] == "execution_not_found"
    assert after_reset.data["execution"] == 1
    assert after_reset.content[0].text == "In [1]:\nafter-reset\n"


def test_mcp_result_mode_only_changes_structured_content() -> None:
    raw = {"ok": True, "execution": 4, "status": "completed", "result_text": "1", "output_text": "Out[4]: 1\n", "output_omitted": False}
    assert make_tool_result("run_cell", raw, "structured").structured_content == raw
    assert make_tool_result("run_cell", raw, "content").structured_content is None


async def test_in_process_session_is_connected_for_the_process_lifetime(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an HTTP request the session is stdio or in-process and never loses its stream."""
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("LOOMMUX_LEASE_MODE", "connection")
    monkeypatch.setenv("LOOMMUX_CONNECTION_GRACE_SECONDS", "0.01")

    async with Client(create_default_mcp()) as client:
        await client.call_tool("status", {})
        await asyncio.sleep(0.05)
        status = await client.call_tool("status", {})

    assert [block.type for block in status.content] == ["text"]
    assert "Lease alert" not in status.content[0].text


async def test_a_call_during_the_restart_tool_gap_waits_instead_of_restarting_again(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(workspace)
    original_restart = IPythonSession.restart
    original_start = KernelSession.start
    restarts = 0
    in_gap = threading.Event()

    def counting_restart(self: IPythonSession) -> dict[str, Any]:
        nonlocal restarts
        restarts += 1
        return original_restart(self)

    def slow_start(self: KernelSession, timeout_seconds: float = 10.0) -> None:
        # restart has already dropped the old kernel; widen that gap so the
        # concurrent call below reliably observes it.
        in_gap.set()
        time.sleep(0.5)
        original_start(self, timeout_seconds)

    async with Client(create_structured_mcp()) as client:
        await client.call_tool("status", {})
        monkeypatch.setattr(IPythonSession, "restart", counting_restart)
        monkeypatch.setattr(KernelSession, "start", slow_start)
        restart = asyncio.create_task(client.call_tool("restart", {}))
        assert await asyncio.to_thread(in_gap.wait, 5)
        status = await client.call_tool("status", {})
        restarted = await restart

    assert restarts == 1
    assert status.data["kernel_started"] is True
    assert status.data["kernel_pid"] == restarted.data["kernel_pid"]


async def test_http_sessions_are_released_when_closed_and_reaped_when_idle(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard the MCP SDK session lifetime loommux depends on (mcp>=1.30.0).

    Older SDKs kept every Streamable HTTP session for the life of the server:
    a session closed with DELETE was never deregistered, and one whose client
    vanished without DELETE had no idle timeout.
    """
    monkeypatch.chdir(workspace)
    app = create_default_mcp().http_app()
    async with app.router.lifespan_context(app):
        manager = next(
            route.app.session_manager
            for route in app.routes
            if getattr(getattr(route, "app", None), "session_manager", None) is not None
        )
        transport = StreamableHttpTransport(
            "http://loommux.test/mcp",
            httpx_client_factory=lambda **options: httpx.AsyncClient(transport=httpx.ASGITransport(app=app), **options),
        )
        async with Client(transport) as client:
            await client.list_tools()
            assert len(manager._server_instances) == 1
        await asyncio.sleep(0.1)

        assert manager._server_instances == {}
        assert manager.session_idle_timeout is not None
