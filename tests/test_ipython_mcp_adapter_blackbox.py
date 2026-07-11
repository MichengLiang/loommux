from __future__ import annotations

import stat
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from loommux.adapter import IPythonMCPAdapter
from loommux.mcp_ipython_server import create_mcp

EXPECTED_TOOLS = {
    "run_python",
    "python_status",
    "python_execution_status",
    "read_python_output",
    "search_python_output",
    "wait_python",
    "interrupt_python",
    "reset_python",
}


@pytest.fixture
async def client(valid_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Client[Any]]:
    monkeypatch.chdir(valid_workspace)
    async with Client(create_mcp()) as mcp_client:
        yield mcp_client


@pytest.fixture
def valid_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    write_python_wrapper(workspace)
    return workspace


def write_python_wrapper(workspace: Path) -> Path:
    python_path = workspace / ".venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text(f'#!/bin/sh\nexec {sys.executable} "$@"\n')
    python_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return python_path


@pytest.fixture
def call(client: Client[Any]) -> Callable[[str, dict[str, Any] | None], Any]:
    async def _call(name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = await client.call_tool(name, arguments or {})
        return result.data

    return _call


@pytest.fixture
def raw_call(client: Client[Any]) -> Callable[[str, dict[str, Any] | None], Any]:
    async def _call(name: str, arguments: dict[str, Any] | None = None) -> Any:
        return await client.call_tool(name, arguments or {})

    return _call


def result_text(result: Any) -> str:
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    return result.content[0].text


async def test_tool_result_preserves_structured_data_and_adds_pretty_status_content(raw_call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    result = await raw_call("python_status")

    assert result.data["ok"] is True
    assert result.data["workspace"] == str(valid_workspace)
    assert result.data["python"] == str(Path(sys.executable).absolute())
    assert result.data["kernel_started"] is True
    assert result.data["busy"] is False
    assert result.data["current_execution_id"] is None

    text = result_text(result)
    assert text.startswith("kernel: idle\nworkspace: ")
    assert not text.lstrip().startswith("{")


async def test_run_python_success_content_includes_all_nonempty_output_blocks(raw_call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    result = await raw_call("run_python", {"freeform": "import sys\nprint('stdout-line')\nprint('stderr-line', file=sys.stderr)\n'RESULT-LINE'"})

    assert result.data["status"] == "completed"
    assert result.data["stdout"] == "stdout-line\n"
    assert result.data["stderr"] == "stderr-line\n"
    assert result.data["result_text"] == "'RESULT-LINE'"
    assert result.data["output_log"] == f"python-output:{result.data['execution_id']}"
    assert "logs" not in result.data

    text = result_text(result)
    assert text.startswith("stdout-line\nstderr-line\nOut[")
    assert text == result.data["output_text"]
    assert result.data["execution_id"] not in text
    assert result.data["output_log"] not in text
    assert "result_text:" not in text
    assert "stdout:" not in text
    assert "stderr:" not in text
    assert "logs" not in text


async def test_run_python_error_content_includes_error_summary_and_traceback_log(raw_call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    result = await raw_call("run_python", {"freeform": "def explode():\n    return 1 / 0\nexplode()"})

    assert result.data["status"] == "error"
    assert result.data["error"]["ename"] == "ZeroDivisionError"
    assert result.data["error"]["evalue"] == "division by zero"
    assert result.data["error"]["traceback_log"] == f"{result.data['output_log']}/traceback"
    assert "traceback" not in result.data["error"]
    assert "logs" not in result.data

    traceback = await raw_call("read_python_output", {"output_log": result.data["output_log"], "stream": "traceback"})
    assert "ZeroDivisionError" in traceback.data["text"]

    text = result_text(result)
    assert not text.startswith("错误：execution")
    assert "ZeroDivisionError: division by zero" in text
    assert text == result.data["output_text"]
    assert result.data["execution_id"] not in text
    assert result.data["output_log"] not in text
    assert "error:" not in text


async def test_python_execution_status_pretty_text_handles_real_error_execution(raw_call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    result = await raw_call("run_python", {"freeform": "1 / 0"})
    status = await raw_call("python_execution_status", {"execution_id": result.data["execution_id"]})

    text = result_text(status)
    assert text.startswith(f"execution {result.data['execution_id']}: error\nerror: ZeroDivisionError: division by zero\ntraceback: {result.data['output_log']}/traceback\nlog: {result.data['output_log']}")
    assert "tool failed" not in text


async def test_running_busy_and_execution_not_found_content_front_loads_state(raw_call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    not_found = await raw_call("read_python_output")
    assert result_text(not_found) == "execution_not_found: execution was not found"

    running = await raw_call("run_python", {"freeform": "# loommux: timeout_seconds=0.1\nimport time\nprint('partial', flush=True)\ntime.sleep(1)"})
    busy = await raw_call("run_python", {"freeform": "queued_value = 123"})

    assert running.data["status"] == "running"
    assert busy.data["status"] == "busy"
    assert result_text(running) == "Python execution is still running. Use wait_python() to wait, python_status() to check its state, or read_python_output() to inspect available output."
    assert result_text(busy) == "busy: kernel is already executing code"

    await raw_call("wait_python", {"execution_id": running.data["execution_id"], "timeout_seconds": 5})


async def test_interrupt_and_reset_content_are_pretty_and_structured_data_is_unchanged(raw_call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    running = await raw_call("run_python", {"freeform": "# loommux: timeout_seconds=0.1\nimport time\nwhile True:\n    time.sleep(0.1)"})
    interrupted = await raw_call("interrupt_python")

    assert interrupted.data["status"] == "interrupt_sent"
    assert interrupted.data["execution_id"] == running.data["execution_id"]
    assert result_text(interrupted).splitlines()[0] == f"中断：已向 execution {running.data['execution_id']} 发送 interrupt。"

    await raw_call("wait_python", {"execution_id": running.data["execution_id"], "timeout_seconds": 5})
    reset = await raw_call("reset_python")

    assert reset.data["status"] == "restarted"
    assert reset.data["busy"] is False
    assert result_text(reset).splitlines()[0] == "重置：kernel 已重启。"


async def test_workspace_is_started_from_the_server_process_cwd(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    status = await call("python_status")

    assert status["ok"] is True
    assert status["workspace"] == str(valid_workspace)
    assert status["python"] == str(Path(sys.executable).absolute())
    assert status["kernel_started"] is True
    assert status["busy"] is False
    assert status["current_execution_id"] is None


async def test_exec_001_state_is_retained_across_runs(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    first = await call("run_python", {"freeform": "x = 41"})
    second = await call("run_python", {"freeform": "x + 1"})

    assert first["status"] == "completed"
    assert second["ok"] is True
    assert second["status"] == "completed"
    assert "42" in second["result_text"]
    assert first["kernel"]["kernel_pid"] == second["kernel"]["kernel_pid"]
    assert first["execution_id"] == "exec-000001"
    assert second["execution_id"] == "exec-000002"


async def test_exec_002_stdout_is_collected(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    result = await call("run_python", {"freeform": "print('hello')"})

    assert result["status"] == "completed"
    assert "hello" in result["stdout"]
    assert "hello" not in result["stderr"]


async def test_exec_003_stderr_is_collected(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    result = await call("run_python", {"freeform": "import sys; print('bad', file=sys.stderr)"})

    assert result["status"] == "completed"
    assert "bad" in result["stderr"]


async def test_exec_004_python_exception_returns_error_without_crashing_kernel(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    result = await call("run_python", {"freeform": "1 / 0"})

    assert result["ok"] is False
    assert result["status"] == "error"
    assert result["error"]["ename"] == "ZeroDivisionError"
    assert (await call("python_status"))["kernel_started"] is True
    assert (await call("run_python", {"freeform": "6 * 7"}))["result_text"] == "42"


async def test_exec_005_and_006_timeout_returns_running_then_wait_completes(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    running = await call("run_python", {"freeform": "# loommux: timeout_seconds=0.1\nimport time\ntime.sleep(1)\n42"})

    assert running["ok"] is True
    assert running["status"] == "running"
    assert running["execution_id"]
    assert (await call("python_status"))["busy"] is True

    completed = await call("wait_python", {"execution_id": running["execution_id"], "timeout_seconds": 5})
    assert completed["status"] == "completed"
    assert "42" in completed["result_text"]
    assert (await call("python_status"))["busy"] is False


async def test_exec_007_running_output_can_be_read(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    running = await call("run_python", {"freeform": "# loommux: timeout_seconds=0.2\nimport time\nfor i in range(3):\n    print(i, flush=True)\n    time.sleep(0.4)"})
    snapshot = await call("read_python_output", {"execution_id": running["execution_id"]})

    assert snapshot["ok"] is True
    assert snapshot["execution_id"] == running["execution_id"]

    completed = await call("wait_python", {"execution_id": running["execution_id"], "timeout_seconds": 5})
    assert completed["status"] == "completed"
    assert "0" in completed["stdout"]
    assert "1" in completed["stdout"]
    assert "2" in completed["stdout"]


async def test_exec_008_busy_run_python_does_not_queue(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    running = await call("run_python", {"freeform": "# loommux: timeout_seconds=0.1\nimport time\ntime.sleep(1)"})
    busy = await call("run_python", {"freeform": "queued_value = 123"})

    assert busy["ok"] is False
    assert busy["status"] == "busy"
    assert busy["current_execution_id"] == running["execution_id"]

    await call("wait_python", {"execution_id": running["execution_id"], "timeout_seconds": 5})
    assert (await call("run_python", {"freeform": "'queued_value' in globals()"}))["result_text"] == "False"


async def test_ctrl_001_interrupt_running_execution(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    running = await call("run_python", {"freeform": "# loommux: timeout_seconds=0.1\nimport time\nwhile True:\n    time.sleep(0.1)"})
    interrupted = await call("interrupt_python")

    assert interrupted["status"] == "interrupt_sent"
    assert interrupted["execution_id"] == running["execution_id"]

    final = await call("wait_python", {"execution_id": running["execution_id"], "timeout_seconds": 5})
    assert final["status"] in {"error", "interrupted"}
    assert final["status"] == "interrupted" or final["error"]["ename"] == "KeyboardInterrupt"
    assert (await call("run_python", {"freeform": "21 * 2"}))["result_text"] == "42"


async def test_ctrl_002_reset_restarts_kernel_and_clears_state(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    await call("run_python", {"freeform": "x = 41"})
    old_pid = (await call("python_status"))["kernel_pid"]
    reset = await call("reset_python")
    new_pid = (await call("python_status"))["kernel_pid"]
    visible = await call("run_python", {"freeform": "'x' in globals()"})

    assert reset["status"] == "restarted"
    assert new_pid != old_pid
    assert visible["result_text"] == "False"


async def test_ctrl_003_reset_kills_running_execution_and_new_kernel_can_run(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    running = await call("run_python", {"freeform": "# loommux: timeout_seconds=0.1\nimport time\ntime.sleep(10)"})
    reset = await call("reset_python")
    old_status = await call("python_execution_status", {"execution_id": running["execution_id"]})

    assert reset["ok"] is True
    assert reset["status"] == "restarted"
    assert old_status["status"] == "killed"
    assert (await call("python_status"))["busy"] is False
    assert (await call("run_python", {"freeform": "40 + 2"}))["result_text"] == "42"


async def test_python_execution_status_pretty_text_handles_killed_execution(raw_call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    running = await raw_call("run_python", {"freeform": "# loommux: timeout_seconds=0.1\nimport time\ntime.sleep(10)"})
    await raw_call("reset_python")
    old_status = await raw_call("python_execution_status", {"execution_id": running.data["execution_id"]})

    text = result_text(old_status)
    assert text.startswith(f"execution {running.data['execution_id']}: killed\nlog: {running.data['output_log']}")
    assert "tool failed" not in text
    assert (await raw_call("run_python", {"freeform": "40 + 2"})).data["result_text"] == "42"


async def test_read_and_wait_default_to_current_or_last_execution(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    assert (await call("read_python_output"))["status"] == "execution_not_found"
    result = await call("run_python", {"freeform": "99"})

    assert (await call("read_python_output"))["execution_id"] == result["execution_id"]
    assert (await call("wait_python", {"timeout_seconds": 1}))["execution_id"] == result["execution_id"]
    assert (await call("read_python_output", {"execution_id": "exec-missing"}))["status"] == "execution_not_found"


async def test_execution_status_returns_log_handles_without_full_log_body(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    result = await call("run_python", {"freeform": "print('status-log-body')\n123"})
    status = await call("python_execution_status", {"execution_id": result["execution_id"]})

    assert status["ok"] is True
    assert status["execution_id"] == result["execution_id"]
    assert status["output_log"] == f"python-output:{result['execution_id']}"
    assert status["output_total_lines"] >= 2
    assert "logs" not in status
    assert "stdout" not in status
    assert "stderr" not in status
    assert "result_text" not in status


async def test_read_python_output_reads_line_ranges_and_stream_handles(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    result = await call("run_python", {"freeform": "import sys\nfor value in ['alpha', 'beta-match', 'gamma', 'delta']:\n    print(value)\nprint('stderr-payload', file=sys.stderr)"})
    output_log = result["output_log"]

    head = await call("read_python_output", {"output_log": output_log, "stream": "stdout", "line_range": ":2", "show_line_numbers": True})
    tail = await call("read_python_output", {"output_log": output_log, "stream": "stdout", "line_range": "-2:", "show_line_numbers": True})
    single = await call("read_python_output", {"output_log": output_log, "stream": "stdout", "line_range": "2:2"})
    clipped = await call("read_python_output", {"output_log": output_log, "stream": "stdout", "line_range": "2:2", "max_chars": 4})
    suffixed = await call("read_python_output", {"output_log": f"{output_log}/stdout", "line_range": "1:1"})
    stderr = await call("read_python_output", {"output_log": output_log, "stream": "stderr"})
    conflicting = await call("read_python_output", {"output_log": f"{output_log}/stderr", "stream": "stdout"})

    assert head["text"] == "1 | alpha\n2 | beta-match"
    assert tail["text"] == "3 | gamma\n4 | delta"
    assert single["text"] == "beta-match"
    assert clipped["text"] == "beta...[6 chars omitted]"
    assert "status" not in head
    assert "error" not in head
    assert head["total_lines"] == 4
    assert head["returned_lines"] == 2
    assert head["stream"] == "stdout"
    assert head["output_log"] == f"{output_log}/stdout"
    assert suffixed["text"] == "alpha"
    assert stderr["stream"] == "stderr"
    assert stderr["output_log"] == f"{output_log}/stderr"
    assert stderr["text"] == "stderr-payload"
    assert conflicting["status"] == "invalid_output_log"


async def test_search_python_output_supports_literal_regex_and_context(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    result = await call("run_python", {"freeform": "for value in ['alpha', 'beta-match', 'gamma', 'delta-match']:\n    print(value)"})
    output_log = result["output_log"]

    literal = await call("search_python_output", {"output_log": output_log, "stream": "stdout", "query": "match", "query_mode": "literal", "context_before": 1})
    regex = await call("search_python_output", {"output_log": output_log, "stream": "stdout", "query": "^(alpha|gamma)$", "query_mode": "regex"})
    none = await call("search_python_output", {"output_log": output_log, "stream": "stdout", "query": "missing", "query_mode": "literal"})
    conflicting = await call("search_python_output", {"output_log": f"{output_log}/stderr", "stream": "stdout", "query": "match", "query_mode": "literal"})

    assert literal["matched_lines"] == 2
    assert "C 1 | alpha" in literal["text"]
    assert "M 2 | beta-match" in literal["text"]
    assert "M 4 | delta-match" in literal["text"]
    assert regex["matched_lines"] == 2
    assert "M 1 | alpha" in regex["text"]
    assert "M 3 | gamma" in regex["text"]
    assert none["ok"] is True
    assert none["matched_lines"] == 0
    assert none["text"] == ""
    assert literal["stream"] == "stdout"
    assert conflicting["status"] == "invalid_output_log"


async def test_run_python_omits_large_output_body_but_keeps_log_handle(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    result = await call("run_python", {"freeform": "for i in range(301):\n    print(f'line-{i:03d}')"})
    tail = await call("read_python_output", {"output_log": result["output_log"], "stream": "stdout", "line_range": "-2:", "show_line_numbers": True})

    assert result["status"] == "completed"
    assert result["output_omitted"] is True
    assert result["output_line_limit"] == 300
    assert result["output_total_lines"] == 301
    assert result["stdout"] == ""
    assert result["stderr"] == ""
    assert result["result_text"] == ""
    assert result["output_log"] == f"python-output:{result['execution_id']}"
    assert "logs" not in result
    assert tail["text"] == "300 | line-299\n301 | line-300"


async def test_run_python_timeout_omits_partial_body_but_keeps_log_handle(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    result = await call("run_python", {"freeform": "# loommux: timeout_seconds=0.1\nimport time\nprint('partial-line', flush=True)\ntime.sleep(1)"})
    output = await call("read_python_output", {"output_log": result["output_log"], "stream": "stdout", "line_range": "1:1"})

    assert result["status"] == "running"
    assert result["output_omitted"] is True
    assert result["output_omitted_reason"] == "running"
    assert result["stdout"] == ""
    assert result["stderr"] == ""
    assert result["result_text"] == ""
    assert "logs" not in result
    assert output["text"] == "partial-line"

    await call("wait_python", {"execution_id": result["execution_id"], "timeout_seconds": 5})


async def test_api_001_and_002_tool_surface_is_exact_and_has_no_truncation_parameters(client: Client[Any]) -> None:
    tools = await client.list_tools()
    names = {tool.name for tool in tools}
    run_python = next(tool for tool in tools if tool.name == "run_python")
    wait_python = next(tool for tool in tools if tool.name == "wait_python")
    python_execution_status = next(tool for tool in tools if tool.name == "python_execution_status")
    read_python_output = next(tool for tool in tools if tool.name == "read_python_output")
    search_python_output = next(tool for tool in tools if tool.name == "search_python_output")
    run_properties = run_python.inputSchema["properties"]
    read_properties = read_python_output.inputSchema["properties"]
    search_properties = search_python_output.inputSchema["properties"]
    execution_status_description = python_execution_status.description or ""
    read_description = read_python_output.description or ""
    search_description = search_python_output.description or ""

    assert names == EXPECTED_TOOLS
    assert "start_python" not in names
    assert set(run_properties) == {"freeform"}
    assert run_properties["freeform"] == {"type": "string"}
    assert run_python.inputSchema["required"] == ["freeform"]
    assert run_python.inputSchema["additionalProperties"] is False
    assert run_properties["freeform"]["type"] == "string"
    assert "code" not in run_properties
    assert "timeout_seconds" not in run_properties
    assert "max_output_chars" not in run_properties
    assert all("truncate" not in name.lower() for name in run_properties)
    run_description = run_python.description or ""
    assert "# loommux: timeout_seconds=120" in run_description
    assert "输入\n----" in run_description
    assert "等待上限\n--------" in run_description
    assert "返回表面\n--------" in run_description
    assert "后续工具\n--------" in run_description
    assert "10 秒" in run_description
    assert "python-output:<execution_id>" in run_description
    assert "/stdout" in run_description
    assert "/stderr" in run_description
    assert "/result" in run_description
    assert "/traceback" in run_description
    assert "LOOMMUX_RUN_TIMEOUT_SECONDS" not in run_description
    assert "timeout_seconds = 120" not in run_description
    assert "read_python_output" in run_description
    assert "search_python_output" in run_description
    for description in (wait_python.description or "",):
        assert "python-output:<execution_id>" in description
        assert "/stdout" in description
        assert "/stderr" in description
        assert "/result" in description
        assert "/traceback" in description
        assert "read_python_output" in description
        assert "search_python_output" in description
        assert "python_execution_status" in description
    for description in (execution_status_description, read_description, search_description):
        assert "python-output:<execution_id>" in description
        assert "/stdout" in description
        assert "/stderr" in description
        assert "/result" in description
        assert "/traceback" in description
    assert "stream" in read_description
    assert "line_range" in read_description
    assert ":10" in read_description
    assert "-10:" in read_description
    assert "max_chars" in read_description
    assert "query_mode" in search_description
    assert "literal" in search_description
    assert "regex" in search_description
    assert "auto" in search_description
    assert "context_before" in search_description
    assert "context_after" in search_description
    assert "ignore_case" in search_description
    assert "stream" in read_properties
    assert "stream" in search_properties


def test_api_003_stdio_client_example_does_not_import_server_module() -> None:
    source = Path("mcp_ipython_client.py").read_text()

    assert "from mcp_ipython import" not in source
    assert "import mcp_ipython" not in source
    assert "StdioTransport" in source
    assert "mcp_ipython_server.py" in source


def test_adapter_validation_and_kernel_not_started_branches(tmp_path: Path) -> None:
    adapter = IPythonMCPAdapter()
    adapter.workspace = tmp_path
    adapter.python_path = tmp_path / ".venv" / "bin" / "python"

    assert adapter.run_python(123)["status"] == "invalid_code"  # type: ignore[arg-type]
    assert adapter.wait_python(timeout_seconds="bad")["status"] == "invalid_timeout"  # type: ignore[arg-type]
    assert adapter.run_python("1 + 1")["status"] == "kernel_not_started"
    assert adapter.interrupt_python()["status"] == "kernel_not_started"


def test_adapter_start_workspace_failure_does_not_restore_old_kernel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class OldKernel:
        pid = 12345
        latest_execution_count = 7
        shutdown_called = False

        def shutdown(self) -> None:
            self.shutdown_called = True

    class FailingKernel:
        pid = None

        def __init__(self, workspace: Path, python_path: Path, on_idle: Callable[..., None]) -> None:
            self.workspace = workspace
            self.python_path = python_path
            self.on_idle = on_idle

        def start(self) -> None:
            raise RuntimeError("boom")

    target_workspace = tmp_path / "target"
    target_python = write_python_wrapper(target_workspace)
    old_kernel = OldKernel()
    adapter = IPythonMCPAdapter()
    adapter.workspace = tmp_path / "old"
    adapter.python_path = tmp_path / "old" / ".venv" / "bin" / "python"
    adapter.kernel = old_kernel  # type: ignore[assignment]
    adapter.current_execution_id = "exec-000001"
    monkeypatch.setattr("loommux.adapter.KernelSession", FailingKernel)

    result = adapter.start_workspace(target_workspace, target_python)
    status = adapter.python_status()
    rerun = adapter.run_python("1 + 1")

    assert result["ok"] is False
    assert result["status"] == "kernel_start_failed"
    assert result["workspace"] == str(target_workspace)
    assert result["python"] == str(target_python)
    assert old_kernel.shutdown_called is True
    assert status["workspace"] == str(target_workspace)
    assert status["python"] == str(target_python)
    assert status["kernel_started"] is False
    assert status["current_execution_id"] is None
    assert rerun["status"] == "kernel_not_started"
