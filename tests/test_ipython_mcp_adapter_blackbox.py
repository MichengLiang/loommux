from __future__ import annotations

import stat
import sys
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


def test_busy_submission_reports_running_integer_without_queueing(adapter: IPythonMCPAdapter) -> None:
    running = adapter.run_python("# loommux: timeout_seconds=0.1\nimport time\ntime.sleep(1)")
    busy = adapter.run_python("'not queued'")

    assert running["execution"] == 1
    assert running["status"] == "running"
    assert busy == {"ok": False, "status": "busy", "execution": 1, "message": "kernel is already executing code"}
    assert adapter.wait_python(1, 3)["status"] == "completed"
    assert len(adapter.executions) == 1


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
