from __future__ import annotations

import stat
import sys
import time
from pathlib import Path

import pytest

from loommux.adapter import IPythonMCPAdapter


@pytest.fixture
def adapter(tmp_path: Path) -> IPythonMCPAdapter:
    workspace = tmp_path / "workspace"
    python = workspace / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f'#!/bin/sh\nexec {sys.executable} "$@"\n')
    python.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    value = IPythonMCPAdapter()
    assert value.start_workspace(workspace, python)["ok"] is True
    yield value
    value.close()


def test_allocates_integer_sequence_and_selects_exact_record(adapter: IPythonMCPAdapter) -> None:
    first = adapter.run_python("print('one')")
    second = adapter.run_python("print('two')")
    third = adapter.run_python("3 * 7")

    assert [first["execution"], second["execution"], third["execution"]] == [1, 2, 3]
    assert adapter.read_python_output(2, "stdout")["text"] == "two"
    assert adapter.wait_python(2)["execution"] == 2
    assert adapter.python_execution_status(2)["execution"] == 2
    assert adapter.read_python_output(99)["status"] == "execution_not_found"


def test_omitted_selection_uses_current_then_recent_and_empty_adapter_is_not_found(tmp_path: Path) -> None:
    adapter = IPythonMCPAdapter()
    assert adapter.python_execution_status()["status"] == "execution_not_found"
    try:
        workspace = tmp_path / "workspace"
        python = workspace / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text(f'#!/bin/sh\nexec {sys.executable} "$@"\n')
        python.chmod(0o700)
        adapter.start_workspace(workspace, python)
        completed = adapter.run_python("'last'")
        assert adapter.wait_python()["execution"] == completed["execution"]
        assert adapter.read_python_output()["execution"] == completed["execution"]
    finally:
        adapter.close()


def test_workspace_start_retries_one_transient_kernel_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    python = workspace / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f'#!/bin/sh\nexec {sys.executable} "$@"\n')
    python.chmod(0o700)

    class ControlledKernel:
        def __init__(self, should_fail: bool) -> None:
            self.should_fail = should_fail
            self.pid = 123
            self.latest_execution_count = 0

        def start(self) -> None:
            if self.should_fail:
                raise RuntimeError("transient startup failure")

        def shutdown(self) -> None:
            pass

        def is_alive(self) -> bool:
            return True

        def set_monitor_callbacks(self, *_callbacks: object) -> None:
            pass

    adapter = IPythonMCPAdapter()
    kernels = [ControlledKernel(True), ControlledKernel(False)]
    monkeypatch.setattr(adapter, "_new_kernel_session", lambda *_args: kernels.pop(0))
    try:
        started = adapter.start_workspace(workspace, python)
        assert started["ok"] is True
        assert not kernels
    finally:
        adapter.close()


def test_busy_submission_reports_running_integer_without_queueing(adapter: IPythonMCPAdapter) -> None:
    running = adapter.run_python("# loommux: timeout_seconds=0.1\nimport time\ntime.sleep(1)")
    busy = adapter.run_python("'not queued'")

    assert running["execution"] == 1
    assert running["status"] == "running"
    assert busy == {"ok": False, "status": "busy", "execution": 1, "message": "kernel is already executing code"}
    assert adapter.wait_python(1, 3)["status"] == "completed"
    assert len(adapter.executions) == 1


def test_full_output_directive_returns_complete_long_combined_output(adapter: IPythonMCPAdapter) -> None:
    response = adapter.run_python("# loommux: full_output\nprint('\\n'.join(f'line-{number}' for number in range(301)))")

    assert response["status"] == "completed"
    assert response["full_output_requested"] is True
    assert response["output_omitted"] is False
    assert response["output_text"].splitlines() == [f"line-{number}" for number in range(301)]


def test_full_output_directive_preserves_the_combined_iopub_order(adapter: IPythonMCPAdapter) -> None:
    response = adapter.run_python("# loommux: full_output\nimport sys\nprint('stdout')\nprint('stderr', file=sys.stderr)\n'display'")
    output = response["output_text"]

    assert response["output_omitted"] is False
    assert output.index("stdout") < output.index("stderr") < output.index("Out[1]: 'display'")


def test_unmarked_long_combined_output_keeps_the_default_omission_rule(adapter: IPythonMCPAdapter) -> None:
    marked = adapter.run_python("# loommux: full_output\nprint('marked only')")
    response = adapter.run_python("print('\\n'.join(f'line-{number}' for number in range(301)))")

    assert marked["output_omitted"] is False
    assert response["status"] == "completed"
    assert response["full_output_requested"] is False
    assert response["output_omitted"] is True
    assert response["output_omitted_reason"] == "line_limit_exceeded"
    assert "output_text" not in response


def test_full_output_directive_survives_running_wait_error_and_reset(adapter: IPythonMCPAdapter) -> None:
    running = adapter.run_python("# loommux: timeout_seconds=0.1\n# loommux: full_output\nimport time\ntime.sleep(0.3)\nprint('\\n'.join(f'wait-{number}' for number in range(301)))")
    assert running["status"] == "running"
    assert running["output_omitted_reason"] == "running"

    completed = adapter.wait_python(running["execution"], timeout_seconds=3)
    assert completed["status"] == "completed"
    assert completed["output_text"].splitlines() == [f"wait-{number}" for number in range(301)]

    failed = adapter.run_python("# loommux: full_output\nprint('before failure')\nraise RuntimeError('expected failure')")
    assert failed["status"] == "error"
    assert failed["output_omitted"] is False
    assert "before failure" in failed["output_text"]
    assert "RuntimeError: expected failure" in failed["output_text"]

    killed = adapter.run_python("# loommux: timeout_seconds=0.1\n# loommux: full_output\nimport time\nprint('before reset', flush=True)\ntime.sleep(5)")
    assert killed["status"] == "running"
    time.sleep(0.2)
    adapter.reset_python()
    reset_result = adapter.wait_python(killed["execution"])
    assert reset_result["status"] == "killed"
    assert reset_result["output_omitted"] is False
    assert "before reset" in reset_result["output_text"]


def test_full_output_directive_remains_a_kernel_comment(adapter: IPythonMCPAdapter) -> None:
    submitted = adapter.run_python("# loommux: full_output\nvalue = 'unchanged'")
    namespace = adapter.run_python("('full_output' in globals(), value)")

    assert submitted["status"] == "completed"
    assert "Out[2]: (False, 'unchanged')" in namespace["output_text"]


def test_reset_preserves_records_and_sequence_and_reauthors_out_label(adapter: IPythonMCPAdapter) -> None:
    first = adapter.run_python("'before reset'")
    reset = adapter.reset_python()
    second = adapter.run_python("'after reset'")

    assert reset["status"] == "restarted"
    assert first["execution"] == 1
    assert second["execution"] == 2
    assert "Out[2]: 'after reset'" in str(adapter.read_python_output(2)["text"])
    assert "Out[1]: 'before reset'" in str(adapter.read_python_output(1)["text"])


def test_reset_kills_running_execution_but_keeps_it_readable(adapter: IPythonMCPAdapter) -> None:
    running = adapter.run_python("# loommux: timeout_seconds=0.1\nimport time\ntime.sleep(5)")
    adapter.reset_python()

    status = adapter.python_execution_status(running["execution"])
    assert status["status"] == "killed"
    assert status["execution"] == 1


def test_stream_read_search_and_invalid_inputs(adapter: IPythonMCPAdapter) -> None:
    result = adapter.run_python("import sys\nprint('alpha')\nprint('warning', file=sys.stderr)\n'omega'")
    execution = result["execution"]

    assert adapter.read_python_output(execution, "stderr")["text"] == "warning"
    assert "M 1 | alpha" in str(adapter.search_python_output("alpha", execution, "stdout", "literal")["text"])
    assert adapter.read_python_output(execution, "invalid")["status"] == "invalid_stream"
    assert adapter.python_execution_status(-1)["status"] == "execution_not_found"


def test_adapter_reports_invalid_operations_and_idle_interrupt(adapter: IPythonMCPAdapter, tmp_path: Path) -> None:
    assert adapter.run_python(1)["status"] == "invalid_code"  # type: ignore[arg-type]
    assert adapter.wait_python(timeout_seconds=0)["status"] == "invalid_timeout"
    assert adapter.interrupt_python()["status"] == "idle"
    assert adapter.reset_python()["status"] == "restarted"
    assert adapter.python_status()["recent_execution"] is None

    unstarted = IPythonMCPAdapter()
    try:
        assert unstarted.reset_python()["status"] == "workspace_not_set"
        assert unstarted.python_status()["kernel_started"] is False
    finally:
        unstarted.close()

    invalid = IPythonMCPAdapter()
    try:
        missing = tmp_path / "missing"
        assert invalid.start_workspace(missing, Path(sys.executable))["status"] == "workspace_not_found"
        not_python = tmp_path / "not-python"
        not_python.write_text("no")
        not_python.chmod(0o600)
        assert invalid.start_workspace(tmp_path, not_python)["status"] == "python_not_executable"
    finally:
        invalid.close()
