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
    "set_workspace",
    "run_python",
    "python_status",
    "read_python_output",
    "wait_python",
    "interrupt_python",
    "reset_python",
}


@pytest.fixture
async def client() -> AsyncIterator[Client[Any]]:
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
    python_path.write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n")
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


async def test_tool_result_preserves_structured_data_and_adds_pretty_status_content(raw_call: Callable[[str, dict[str, Any] | None], Any]) -> None:
    result = await raw_call("python_status")

    assert result.data == {
        "ok": True,
        "workspace": None,
        "python": None,
        "kernel_started": False,
        "kernel_pid": None,
        "busy": False,
        "current_execution_id": None,
        "execution_count": 0,
        "last_execution_id": None,
    }

    text = result_text(result)
    assert text.splitlines()[0] == "状态：workspace 未设置，kernel 未启动。"
    assert "- ok: true" in text
    assert "- kernel_started: false" in text
    assert "- busy: false" in text
    assert not text.lstrip().startswith("{")


async def test_run_python_success_content_includes_all_nonempty_output_blocks(raw_call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    await raw_call("set_workspace", {"path": str(valid_workspace)})
    result = await raw_call("run_python", {"code": "import sys\nprint('stdout-line')\nprint('stderr-line', file=sys.stderr)\n'RESULT-LINE'"})

    assert result.data["status"] == "completed"
    assert result.data["stdout"] == "stdout-line\n"
    assert result.data["stderr"] == "stderr-line\n"
    assert result.data["result_text"] == "'RESULT-LINE'"

    text = result_text(result)
    assert text.splitlines()[0] == f"完成：execution {result.data['execution_id']} 已完成。"
    assert text.index("result_text:") < text.index("stdout:") < text.index("stderr:")
    assert "'RESULT-LINE'" in text
    assert "stdout-line\n" in text
    assert "stderr-line\n" in text


async def test_run_python_error_content_includes_error_fields_and_full_traceback(raw_call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    await raw_call("set_workspace", {"path": str(valid_workspace)})
    result = await raw_call("run_python", {"code": "def explode():\n    return 1 / 0\nexplode()"})

    assert result.data["status"] == "error"
    traceback = result.data["error"]["traceback"]
    assert traceback

    text = result_text(result)
    assert text.splitlines()[0] == f"错误：execution {result.data['execution_id']} 执行失败。"
    assert "error:" in text
    assert "- ename: ZeroDivisionError" in text
    assert "- evalue: division by zero" in text
    assert "traceback:" in text
    for line in traceback:
        assert line in text


async def test_running_busy_and_execution_not_found_content_front_loads_state(raw_call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    not_found = await raw_call("read_python_output")
    assert result_text(not_found).splitlines()[0] == "前置状态：未找到 execution。"

    await raw_call("set_workspace", {"path": str(valid_workspace)})
    running = await raw_call("run_python", {"code": "import time\nprint('partial', flush=True)\ntime.sleep(1)", "timeout_seconds": 0.1})
    busy = await raw_call("run_python", {"code": "queued_value = 123"})

    assert running.data["status"] == "running"
    assert busy.data["status"] == "busy"
    assert result_text(running).splitlines()[0] == f"运行中：execution {running.data['execution_id']} 仍在执行。"
    assert result_text(busy).splitlines()[0] == f"前置状态：kernel 正在执行 {running.data['execution_id']}，未提交新代码。"

    await raw_call("wait_python", {"execution_id": running.data["execution_id"], "timeout_seconds": 5})


async def test_interrupt_and_reset_content_are_pretty_and_structured_data_is_unchanged(raw_call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    await raw_call("set_workspace", {"path": str(valid_workspace)})
    running = await raw_call("run_python", {"code": "import time\nwhile True:\n    time.sleep(0.1)", "timeout_seconds": 0.1})
    interrupted = await raw_call("interrupt_python")

    assert interrupted.data["status"] == "interrupt_sent"
    assert interrupted.data["execution_id"] == running.data["execution_id"]
    assert result_text(interrupted).splitlines()[0] == f"中断：已向 execution {running.data['execution_id']} 发送 interrupt。"

    await raw_call("wait_python", {"execution_id": running.data["execution_id"], "timeout_seconds": 5})
    reset = await raw_call("reset_python")

    assert reset.data["status"] == "restarted"
    assert reset.data["busy"] is False
    assert result_text(reset).splitlines()[0] == "重置：kernel 已重启。"


async def test_ws_001_initial_status_has_no_workspace(call: Callable[[str, dict[str, Any] | None], Any]) -> None:
    status = await call("python_status")

    assert status["ok"] is True
    assert status["workspace"] is None
    assert status["python"] is None
    assert status["kernel_started"] is False
    assert status["busy"] is False
    assert status["current_execution_id"] is None


async def test_ws_002_set_workspace_starts_kernel(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    result = await call("set_workspace", {"path": str(valid_workspace)})

    assert result["ok"] is True
    assert result["workspace"] == str(valid_workspace.resolve())
    assert result["python"] == str(valid_workspace / ".venv" / "bin" / "python")
    assert result["kernel_started"] is True
    assert isinstance(result["kernel_pid"], int)
    assert result["busy"] is False
    assert result["current_execution_id"] is None
    assert result["execution_count"] == 0


async def test_ws_003_missing_workspace_is_not_created(call: Callable[[str, dict[str, Any] | None], Any], tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    result = await call("set_workspace", {"path": str(missing)})

    assert result["ok"] is False
    assert result["status"] == "workspace_not_found"
    assert missing.exists() is False
    assert (await call("python_status"))["kernel_started"] is False


async def test_ws_004_missing_workspace_python_returns_clear_error(call: Callable[[str, dict[str, Any] | None], Any], tmp_path: Path) -> None:
    workspace = tmp_path / "empty-workspace"
    workspace.mkdir()
    result = await call("set_workspace", {"path": str(workspace)})

    assert result["ok"] is False
    assert result["status"] == "python_not_found"
    assert result["python"] == str(workspace / ".venv" / "bin" / "python")
    assert (await call("python_status"))["kernel_started"] is False


async def test_set_workspace_reports_not_directory_not_executable_and_missing_ipykernel(call: Callable[[str, dict[str, Any] | None], Any], tmp_path: Path) -> None:
    file_workspace = tmp_path / "not-a-dir"
    file_workspace.write_text("")
    assert (await call("set_workspace", {"path": str(file_workspace)}))["status"] == "workspace_not_directory"

    not_executable = tmp_path / "not-executable"
    python_path = not_executable / ".venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("#!/bin/sh\nexit 0\n")
    python_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert (await call("set_workspace", {"path": str(not_executable)}))["status"] == "python_not_executable"

    missing_ipykernel = tmp_path / "missing-ipykernel"
    fake_python = missing_ipykernel / ".venv" / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("#!/bin/sh\nexit 1\n")
    fake_python.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    assert (await call("set_workspace", {"path": str(missing_ipykernel)}))["status"] == "ipykernel_missing"


async def test_set_workspace_resolves_relative_paths_and_tilde(call: Callable[[str, dict[str, Any] | None], Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    relative_workspace = tmp_path / "relative-workspace"
    write_python_wrapper(relative_workspace)
    monkeypatch.chdir(tmp_path)

    relative_result = await call("set_workspace", {"path": "relative-workspace"})
    assert relative_result["ok"] is True
    assert relative_result["workspace"] == str(relative_workspace.resolve())

    home = tmp_path / "home"
    tilde_workspace = home / "tilde-workspace"
    write_python_wrapper(tilde_workspace)
    monkeypatch.setenv("HOME", str(home))

    tilde_result = await call("set_workspace", {"path": "~/tilde-workspace"})
    assert tilde_result["ok"] is True
    assert tilde_result["workspace"] == str(tilde_workspace.resolve())
    assert (await call("read_python_output"))["status"] == "execution_not_found"


async def test_set_workspace_to_missing_path_closes_existing_kernel(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path, tmp_path: Path) -> None:
    await call("set_workspace", {"path": str(valid_workspace)})
    old_pid = (await call("python_status"))["kernel_pid"]
    assert (await call("run_python", {"code": "survivor = 42"}))["status"] == "completed"

    missing = tmp_path / "missing-after-valid"
    result = await call("set_workspace", {"path": str(missing)})
    status = await call("python_status")
    rerun = await call("run_python", {"code": "survivor"})

    assert result["ok"] is False
    assert result["status"] == "workspace_not_found"
    assert result["workspace"] == str(missing)
    assert status["kernel_started"] is False
    assert status["kernel_pid"] is None
    assert status["busy"] is False
    assert status["current_execution_id"] is None
    assert rerun["ok"] is False
    assert rerun["status"] in {"workspace_not_set", "kernel_not_started"}
    assert old_pid is not None


async def test_set_workspace_to_missing_python_closes_existing_kernel(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path, tmp_path: Path) -> None:
    await call("set_workspace", {"path": str(valid_workspace)})
    missing_python_workspace = tmp_path / "missing-python-after-valid"
    missing_python_workspace.mkdir()

    result = await call("set_workspace", {"path": str(missing_python_workspace)})
    status = await call("python_status")
    rerun = await call("run_python", {"code": "1 + 1"})

    assert result["ok"] is False
    assert result["status"] == "python_not_found"
    assert result["python"] == str(missing_python_workspace / ".venv" / "bin" / "python")
    assert status["kernel_started"] is False
    assert status["busy"] is False
    assert status["current_execution_id"] is None
    assert rerun["ok"] is False


async def test_set_workspace_to_missing_ipykernel_closes_existing_running_execution(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path, tmp_path: Path) -> None:
    await call("set_workspace", {"path": str(valid_workspace)})
    running = await call("run_python", {"code": "import time\ntime.sleep(10)", "timeout_seconds": 0.1})
    missing_ipykernel = tmp_path / "missing-ipykernel-after-valid"
    fake_python = missing_ipykernel / ".venv" / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("#!/bin/sh\nexit 1\n")
    fake_python.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    result = await call("set_workspace", {"path": str(missing_ipykernel)})
    killed_output = await call("read_python_output", {"execution_id": running["execution_id"]})
    status = await call("python_status")

    assert result["ok"] is False
    assert result["status"] == "ipykernel_missing"
    assert killed_output["status"] == "killed"
    assert status["kernel_started"] is False
    assert status["busy"] is False
    assert status["current_execution_id"] is None


async def test_run_python_requires_workspace_and_positive_timeout(call: Callable[[str, dict[str, Any] | None], Any]) -> None:
    assert (await call("run_python", {"code": "1 + 1"}))["status"] == "workspace_not_set"
    assert (await call("run_python", {"code": "1 + 1", "timeout_seconds": 0}))["status"] == "invalid_timeout"


async def test_exec_001_state_is_retained_across_runs(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    await call("set_workspace", {"path": str(valid_workspace)})
    first = await call("run_python", {"code": "x = 41"})
    second = await call("run_python", {"code": "x + 1"})

    assert first["status"] == "completed"
    assert second["ok"] is True
    assert second["status"] == "completed"
    assert "42" in second["result_text"]
    assert first["kernel"]["kernel_pid"] == second["kernel"]["kernel_pid"]
    assert first["execution_id"] == "exec-000001"
    assert second["execution_id"] == "exec-000002"


async def test_exec_002_stdout_is_collected(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    await call("set_workspace", {"path": str(valid_workspace)})
    result = await call("run_python", {"code": "print('hello')"})

    assert result["status"] == "completed"
    assert "hello" in result["stdout"]
    assert "hello" not in result["stderr"]


async def test_exec_003_stderr_is_collected(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    await call("set_workspace", {"path": str(valid_workspace)})
    result = await call("run_python", {"code": "import sys; print('bad', file=sys.stderr)"})

    assert result["status"] == "completed"
    assert "bad" in result["stderr"]


async def test_exec_004_python_exception_returns_error_without_crashing_kernel(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    await call("set_workspace", {"path": str(valid_workspace)})
    result = await call("run_python", {"code": "1 / 0"})

    assert result["ok"] is False
    assert result["status"] == "error"
    assert result["error"]["ename"] == "ZeroDivisionError"
    assert (await call("python_status"))["kernel_started"] is True
    assert (await call("run_python", {"code": "6 * 7"}))["result_text"] == "42"


async def test_exec_005_and_006_timeout_returns_running_then_wait_completes(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    await call("set_workspace", {"path": str(valid_workspace)})
    running = await call("run_python", {"code": "import time\ntime.sleep(1)\n42", "timeout_seconds": 0.1})

    assert running["ok"] is True
    assert running["status"] == "running"
    assert running["execution_id"]
    assert (await call("python_status"))["busy"] is True

    completed = await call("wait_python", {"execution_id": running["execution_id"], "timeout_seconds": 5})
    assert completed["status"] == "completed"
    assert "42" in completed["result_text"]
    assert (await call("python_status"))["busy"] is False


async def test_exec_007_running_output_can_be_read(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    await call("set_workspace", {"path": str(valid_workspace)})
    running = await call("run_python", {"code": "import time\nfor i in range(3):\n    print(i, flush=True)\n    time.sleep(0.4)", "timeout_seconds": 0.2})
    snapshot = await call("read_python_output", {"execution_id": running["execution_id"]})

    assert snapshot["status"] in {"running", "completed"}

    completed = await call("wait_python", {"execution_id": running["execution_id"], "timeout_seconds": 5})
    assert completed["status"] == "completed"
    assert "0" in completed["stdout"]
    assert "1" in completed["stdout"]
    assert "2" in completed["stdout"]


async def test_exec_008_busy_run_python_does_not_queue(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    await call("set_workspace", {"path": str(valid_workspace)})
    running = await call("run_python", {"code": "import time\ntime.sleep(1)", "timeout_seconds": 0.1})
    busy = await call("run_python", {"code": "queued_value = 123"})

    assert busy["ok"] is False
    assert busy["status"] == "busy"
    assert busy["current_execution_id"] == running["execution_id"]

    await call("wait_python", {"execution_id": running["execution_id"], "timeout_seconds": 5})
    assert (await call("run_python", {"code": "'queued_value' in globals()"}))["result_text"] == "False"


async def test_ctrl_001_interrupt_running_execution(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    await call("set_workspace", {"path": str(valid_workspace)})
    running = await call("run_python", {"code": "import time\nwhile True:\n    time.sleep(0.1)", "timeout_seconds": 0.1})
    interrupted = await call("interrupt_python")

    assert interrupted["status"] == "interrupt_sent"
    assert interrupted["execution_id"] == running["execution_id"]

    final = await call("wait_python", {"execution_id": running["execution_id"], "timeout_seconds": 5})
    assert final["status"] in {"error", "interrupted"}
    assert final["status"] == "interrupted" or final["error"]["ename"] == "KeyboardInterrupt"
    assert (await call("run_python", {"code": "21 * 2"}))["result_text"] == "42"


async def test_ctrl_002_reset_restarts_kernel_and_clears_state(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    await call("set_workspace", {"path": str(valid_workspace)})
    await call("run_python", {"code": "x = 41"})
    old_pid = (await call("python_status"))["kernel_pid"]
    reset = await call("reset_python")
    new_pid = (await call("python_status"))["kernel_pid"]
    visible = await call("run_python", {"code": "'x' in globals()"})

    assert reset["status"] == "restarted"
    assert new_pid != old_pid
    assert visible["result_text"] == "False"


async def test_ctrl_003_reset_kills_running_execution_and_new_kernel_can_run(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    await call("set_workspace", {"path": str(valid_workspace)})
    running = await call("run_python", {"code": "import time\ntime.sleep(10)", "timeout_seconds": 0.1})
    reset = await call("reset_python")
    old_output = await call("read_python_output", {"execution_id": running["execution_id"]})

    assert reset["ok"] is True
    assert reset["status"] == "restarted"
    assert old_output["status"] == "killed"
    assert (await call("python_status"))["busy"] is False
    assert (await call("run_python", {"code": "40 + 2"}))["result_text"] == "42"


async def test_read_and_wait_default_to_current_or_last_execution(call: Callable[[str, dict[str, Any] | None], Any], valid_workspace: Path) -> None:
    assert (await call("read_python_output"))["status"] == "execution_not_found"
    await call("set_workspace", {"path": str(valid_workspace)})
    result = await call("run_python", {"code": "99"})

    assert (await call("read_python_output"))["execution_id"] == result["execution_id"]
    assert (await call("wait_python", {"timeout_seconds": 1}))["execution_id"] == result["execution_id"]
    assert (await call("read_python_output", {"execution_id": "exec-missing"}))["status"] == "execution_not_found"


async def test_api_001_and_002_tool_surface_is_exact_and_has_no_truncation_parameters(client: Client[Any]) -> None:
    tools = await client.list_tools()
    names = {tool.name for tool in tools}
    run_python = next(tool for tool in tools if tool.name == "run_python")
    properties = run_python.inputSchema["properties"]

    assert names == EXPECTED_TOOLS
    assert "start_python" not in names
    assert set(properties) == {"code", "timeout_seconds"}
    assert "max_output_chars" not in properties
    assert all("truncate" not in name.lower() for name in properties)


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
    assert adapter.run_python("1 + 1", timeout_seconds="bad")["status"] == "invalid_timeout"  # type: ignore[arg-type]
    assert adapter.run_python("1 + 1")["status"] == "kernel_not_started"
    assert adapter.interrupt_python()["status"] == "kernel_not_started"


def test_adapter_set_workspace_start_failure_does_not_restore_old_kernel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    result = adapter.set_workspace(str(target_workspace))
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
