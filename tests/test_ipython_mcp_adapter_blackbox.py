from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from loommux.adapter import IPythonMCPAdapter

TOKEN_LIGHT_MANY_LINES = "\n".join(f"line-{number}" for number in range(301)) + "\n"
TOKEN_HEAVY_LINE = "abcdefghij " * 20
TOKEN_HEAVY_MANY_LINES = f"{TOKEN_HEAVY_LINE}\n" * 301


@pytest.fixture
def adapter(tmp_path: Path) -> IPythonMCPAdapter:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    value = IPythonMCPAdapter()
    assert value.start_workspace(workspace, "launch_cwd")["ok"] is True
    yield value
    value.close()


def test_allocates_integer_sequence_and_selects_exact_record(adapter: IPythonMCPAdapter) -> None:
    first = adapter.run_cell("print('one')")
    second = adapter.run_cell("print('two')")
    third = adapter.run_cell("3 * 7")

    assert [first["execution"], second["execution"], third["execution"]] == [1, 2, 3]
    assert adapter.read_output(2, "stdout")["text"] == "two"
    assert adapter.wait(2)["execution"] == 2
    assert adapter.execution_status(2)["execution"] == 2
    assert adapter.read_output(99)["status"] == "execution_not_found"


def test_omitted_selection_uses_current_then_recent_and_empty_adapter_is_not_found(tmp_path: Path) -> None:
    adapter = IPythonMCPAdapter()
    assert adapter.execution_status()["status"] == "execution_not_found"
    try:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        adapter.start_workspace(workspace, "launch_cwd")
        completed = adapter.run_cell("'last'")
        assert adapter.wait()["execution"] == completed["execution"]
        assert adapter.read_output()["execution"] == completed["execution"]
    finally:
        adapter.close()


def test_workspace_start_retries_one_transient_kernel_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    class ControlledKernel:
        def __init__(self, should_fail: bool) -> None:
            self.should_fail = should_fail
            self.pid = 123
            self.latest_execution_count = 0
            self.shutdown_calls = 0

        def start(self) -> None:
            if self.should_fail:
                raise RuntimeError("transient startup failure")

        def shutdown(self, *, mark_execution_killed: bool = True) -> None:
            self.shutdown_calls += 1

        def is_alive(self) -> bool:
            return True

    adapter = IPythonMCPAdapter()
    failed_kernel = ControlledKernel(True)
    started_kernel = ControlledKernel(False)
    kernels = [failed_kernel, started_kernel]
    monkeypatch.setattr(adapter, "_new_kernel_session", lambda *_args: kernels.pop(0))
    try:
        started = adapter.start_workspace(workspace, "launch_cwd")
        assert started["ok"] is True
        assert not kernels
        assert failed_kernel.shutdown_calls == 1
    finally:
        adapter.close()


def test_busy_submission_reports_running_integer_without_queueing(adapter: IPythonMCPAdapter) -> None:
    running = adapter.run_cell("# loommux: --wait 0.1\nimport time\ntime.sleep(1)")
    busy = adapter.run_cell("'not queued'")

    assert running["execution"] == 1
    assert running["status"] == "running"
    assert busy == {"ok": False, "status": "busy", "execution": 1, "message": "kernel is already executing code"}
    assert adapter.wait(1, 3)["status"] == "completed"
    assert len(adapter.executions) == 1


def test_interrupts_a_running_kernel_cell_through_the_managed_runtime(adapter: IPythonMCPAdapter) -> None:
    running = adapter.run_cell("# loommux: --wait 0.1\nimport time\nprint('started', flush=True)\ntime.sleep(30)")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and "started" not in str(adapter.read_output(running["execution"], "stdout")["text"]):
        time.sleep(0.05)

    interrupted = adapter.interrupt()
    completed = adapter.wait(running["execution"], 3)

    assert running["status"] == "running"
    assert "started" in str(adapter.read_output(running["execution"], "stdout")["text"])
    assert interrupted["status"] == "interrupt_sent"
    assert completed["status"] == "interrupted"
    assert completed["error"] == {"ename": "KeyboardInterrupt", "evalue": ""}


def test_full_output_directive_returns_complete_long_combined_output(adapter: IPythonMCPAdapter) -> None:
    response = adapter.run_cell("# loommux: --full-output\nprint(('abcdefghij ' * 20 + '\\n') * 301, end='')")

    assert response["status"] == "completed"
    assert response["output_omitted"] is False
    assert response["output_text"] == TOKEN_HEAVY_MANY_LINES


def test_full_output_directive_preserves_the_combined_iopub_order(adapter: IPythonMCPAdapter) -> None:
    response = adapter.run_cell("# loommux: --full-output\nimport sys\nprint('stdout')\nprint('stderr', file=sys.stderr)\n'display'")
    output = response["output_text"]

    assert response["output_omitted"] is False
    assert output.index("stdout") < output.index("stderr") < output.index("Out[1]: 'display'")


def test_directive_does_not_create_namespace_control_state(adapter: IPythonMCPAdapter) -> None:
    adapter.run_cell("counter = 0")
    response = adapter.run_cell("# loommux: --full-output\ncounter += 1\ncounter")
    namespace = adapter.run_cell("counter")

    assert response["execution"] == 2
    assert response["output_text"].strip() == "Out[2]: 1"
    assert namespace["output_text"].strip() == "Out[3]: 1"
    assert "loommux" not in str(adapter.run_cell("sorted(name for name in globals() if 'loommux' in name)")["output_text"])


def test_directive_preserves_rich_display_events(adapter: IPythonMCPAdapter) -> None:
    response = adapter.run_cell(
        "# loommux: --full-output\n"
        "from IPython.display import display\n"
        "from PIL import Image\n"
        "print('before-image')\n"
        "display(Image.new('RGB', (1, 1), 'red'))\n"
        "print('after-image')"
    )
    record = adapter.executions[response["execution"]]

    assert response["status"] == "completed"
    assert response["output_text"].index("before-image") < response["output_text"].index("after-image")
    assert record.has_rich_presentation is True


@pytest.mark.skipif(sys.platform == "win32", reason="IPython %%bash requires a POSIX shell")
def test_directive_composes_with_a_bash_cell_magic(adapter: IPythonMCPAdapter) -> None:
    running = adapter.run_cell("# loommux: --wait 0.1\n# loommux: --full-output\n%%bash\nsleep 0.3\nprintf 'bash-finished\\n'")

    assert running["status"] == "running"

    completed = adapter.wait(running["execution"], timeout_seconds=3)

    assert completed["status"] == "completed"
    assert completed["output_text"] == "bash-finished\n"


def test_unmarked_many_line_output_bypasses_the_line_limit_when_it_is_token_light(adapter: IPythonMCPAdapter) -> None:
    response = adapter.run_cell("print('\\n'.join(f'line-{number}' for number in range(301)))")

    assert response["status"] == "completed"
    assert response["output_omitted"] is False
    assert response["output_text"] == TOKEN_LIGHT_MANY_LINES


def test_unmarked_token_heavy_many_line_output_keeps_the_default_omission_rule(adapter: IPythonMCPAdapter) -> None:
    response = adapter.run_cell("print(('abcdefghij ' * 20 + '\\n') * 301, end='')")

    assert response["status"] == "completed"
    assert response["output_omitted"] is True
    assert response["output_omitted_reason"] == "line_limit_exceeded"
    assert "output_text" not in response
    assert response["output_total_lines"] == 301
    assert response["output_total_characters"] == len(TOKEN_HEAVY_MANY_LINES)
    assert response["output_total_utf8_bytes"] == len(TOKEN_HEAVY_MANY_LINES.encode("utf-8"))


def test_full_output_directive_survives_running_wait_error_and_reset(adapter: IPythonMCPAdapter) -> None:
    running = adapter.run_cell("# loommux: --wait 0.1 --full-output\nimport time\ntime.sleep(0.3)\nprint(('abcdefghij ' * 20 + '\\n') * 301, end='')")
    assert running["status"] == "running"
    assert running["output_omitted_reason"] == "running"

    completed = adapter.wait(running["execution"], timeout_seconds=3)
    assert completed["status"] == "completed"
    assert completed["output_text"] == TOKEN_HEAVY_MANY_LINES

    failed = adapter.run_cell("# loommux: --full-output\nprint('before failure')\nraise RuntimeError('expected failure')")
    assert failed["status"] == "error"
    assert failed["output_omitted"] is False
    assert "before failure" in failed["output_text"]
    assert "RuntimeError: expected failure" in failed["output_text"]

    killed = adapter.run_cell("# loommux: --wait 0.1 --full-output\nimport time\nprint('before reset', flush=True)\ntime.sleep(5)")
    assert killed["status"] == "running"
    time.sleep(0.2)
    adapter.restart()
    reset_result = adapter.wait(killed["execution"])
    assert reset_result["status"] == "killed"
    assert reset_result["output_omitted"] is False
    assert "before reset" in reset_result["output_text"]


def test_legacy_key_value_declaration_fails_before_python_execution(adapter: IPythonMCPAdapter) -> None:
    response = adapter.run_cell("# loommux: legacy_key=0.1\nprint('\\n'.join(f'line-{number}' for number in range(301)))")

    assert response["status"] == "invalid_loommux_directive"
    assert "unknown option" in response["message"]


def test_apply_patch_literal_preserves_raw_value_without_archiving_request_source(adapter: IPythonMCPAdapter) -> None:
    source = '''# loommux: --wait 2 --full-output
name = "Ada"
payload = f"""
*** Begin Patch
*** Update File: example.py
@@
+message = r"""
+C:\\new\\temp {name}
+"""
*** End Patch
"""
'''

    submitted = adapter.run_cell(source)
    inspected = adapter.run_cell("payload")
    record = adapter.executions[submitted["execution"]]

    assert submitted["status"] == "completed"
    assert "C:\\\\new\\\\temp {name}" in inspected["output_text"]
    assert "*** Begin Patch" in inspected["output_text"]
    for field in ("code", "author_source", "submitted_source", "apply_patch_transform", "initial_wait_seconds", "control_directives"):
        assert not hasattr(record, field)


def test_apply_patch_literal_is_a_function_argument_and_keeps_later_traceback_lines(adapter: IPythonMCPAdapter) -> None:
    source = '''# loommux: --full-output
received = None
def capture(value):
    global received
    received = value
capture(r"""
*** Begin Patch
*** Add File: captured.txt
+inside = """
+quoted
+"""
*** End Patch
""")
raise RuntimeError("mapped")
'''

    failed = adapter.run_cell(source)
    received = adapter.run_cell("received")

    assert failed["status"] == "error"
    assert "line 13" in failed["output_text"]
    assert received["status"] == "completed"
    assert "*** Begin Patch" in received["output_text"]


def test_reset_preserves_records_and_sequence_and_reauthors_out_label(adapter: IPythonMCPAdapter) -> None:
    first = adapter.run_cell("'before reset'")
    reset = adapter.restart()
    second = adapter.run_cell("# loommux: --full-output\n'after reset'")

    assert reset["status"] == "restarted"
    assert first["execution"] == 1
    assert second["execution"] == 2
    assert "Out[2]: 'after reset'" in str(adapter.read_output(2)["text"])
    assert "Out[1]: 'before reset'" in str(adapter.read_output(1)["text"])


def test_reset_kills_running_execution_but_keeps_it_readable(adapter: IPythonMCPAdapter) -> None:
    running = adapter.run_cell("# loommux: --wait 0.1\nimport time\ntime.sleep(5)")
    adapter.restart()

    status = adapter.execution_status(running["execution"])
    assert status["status"] == "killed"
    assert status["execution"] == 1


def test_invalid_directive_has_no_real_kernel_or_sequence_side_effect(adapter: IPythonMCPAdapter) -> None:
    accepted = adapter.run_cell("'before invalid'")
    invalid = adapter.run_cell("# loommux: --wait 10 --wait 20\nprint('must not run')")
    after = adapter.run_cell("'after invalid'")

    assert invalid["status"] == "invalid_loommux_directive"
    assert "must not run" not in str(adapter.read_output(accepted["execution"])["text"])
    assert after["execution"] == accepted["execution"] + 1


def test_invalid_python_indentation_with_a_valid_directive_reaches_the_kernel(adapter: IPythonMCPAdapter) -> None:
    response = adapter.run_cell("if True:\n    pass\n  pass\n# loommux: --full-output\n")

    assert response["execution"] == 1
    assert response["status"] == "error"
    assert response["error"]["ename"] == "IndentationError"
    assert "unindent does not match any outer indentation level" in response["error"]["evalue"]


def test_inner_directive_text_is_python_data_and_cannot_change_outer_policy(adapter: IPythonMCPAdapter) -> None:
    response = adapter.run_cell('# loommux: --wait 2\npayload = """\n# loommux: --full-output\n"""\nprint(payload)')

    assert response["output_text"].strip() == "# loommux: --full-output"


def test_lone_cr_string_data_does_not_become_a_control_directive(adapter: IPythonMCPAdapter) -> None:
    response = adapter.run_cell('payload = """\r# loommux: --wait 0\r"""\rprint(payload)')

    assert response["status"] == "completed"
    assert response["output_text"].strip() == "# loommux: --wait 0"


def test_f_string_data_does_not_become_a_control_directive(adapter: IPythonMCPAdapter) -> None:
    response = adapter.run_cell('payload = f"""\n# loommux: --wait 0\n"""\nprint(payload)')

    assert response["status"] == "completed"
    assert response["output_text"].strip() == "# loommux: --wait 0"


def test_directives_are_absent_from_real_ipython_history_without_padding(adapter: IPythonMCPAdapter) -> None:
    source = "# loommux: --wait 2\n# loommux: --full-output\nvalue_for_history = 41\nvalue_for_history + 1"

    response = adapter.run_cell(source)
    history = adapter.run_cell("get_ipython().history_manager.input_hist_raw[1]")

    assert response["status"] == "completed"
    assert history["result_text"] == "'value_for_history = 41\\nvalue_for_history + 1'"


@pytest.mark.skipif(sys.platform == "win32", reason="IPython %%bash requires a POSIX shell")
def test_directive_inside_a_magic_body_is_removed_before_that_body_runs(adapter: IPythonMCPAdapter) -> None:
    response = adapter.run_cell("%%bash\n# loommux: --full-output\nprintf 'body-clean\\n'")

    assert response["status"] == "completed"
    assert response["output_text"] == "body-clean\n"


def test_directive_is_removed_before_a_non_comment_magic_body_receives_it(adapter: IPythonMCPAdapter) -> None:
    registered = adapter.run_cell(
        "from IPython.core.magic import register_cell_magic\n"
        "@register_cell_magic\n"
        "def strict_body(_line, cell):\n"
        "    if '# loommux:' in cell:\n"
        "        raise RuntimeError('transport metadata leaked into body')\n"
        "    print(cell)"
    )
    response = adapter.run_cell("# loommux: --full-output\n%%strict_body\nbody language text")

    assert registered["status"] == "completed"
    assert response["status"] == "completed"
    assert response["output_text"] == "body language text\n\n"


def test_public_execution_responses_exclude_consumed_control_details(adapter: IPythonMCPAdapter) -> None:
    response = adapter.run_cell("# loommux: --wait 2 --full-output\nprint('done')")
    waited = adapter.wait(response["execution"])
    status = adapter.execution_status(response["execution"])

    for result in (response, waited, status):
        assert {"initial_wait_seconds", "full_output_requested", "control_directives"}.isdisjoint(result)


def test_stream_read_search_and_invalid_inputs(adapter: IPythonMCPAdapter) -> None:
    result = adapter.run_cell("import sys\nprint('alpha')\nprint('warning', file=sys.stderr)\n'omega'")
    execution = result["execution"]

    assert adapter.read_output(execution, "stderr")["text"] == "warning"
    assert "M 1 | alpha" in str(adapter.search_output("alpha", execution, "stdout", "literal")["text"])
    assert adapter.read_output(execution, "invalid")["status"] == "invalid_stream"
    assert adapter.execution_status(-1)["status"] == "execution_not_found"


def test_adapter_reports_invalid_operations_and_idle_interrupt(adapter: IPythonMCPAdapter, tmp_path: Path) -> None:
    assert adapter.run_cell(1)["status"] == "invalid_code"  # type: ignore[arg-type]
    assert adapter.wait(timeout_seconds=0)["status"] == "invalid_timeout"
    assert adapter.interrupt()["status"] == "idle"
    assert adapter.restart()["status"] == "restarted"
    assert adapter.status()["recent_execution"] is None

    unstarted = IPythonMCPAdapter()
    try:
        assert unstarted.restart()["status"] == "workspace_not_set"
        assert unstarted.status()["kernel_started"] is False
    finally:
        unstarted.close()

    invalid = IPythonMCPAdapter()
    try:
        missing = tmp_path / "missing"
        assert invalid.start_workspace(missing, "launch_cwd")["status"] == "workspace_not_found"
    finally:
        invalid.close()
