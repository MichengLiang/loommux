from __future__ import annotations

from loommux.presentation import format_tool_result_text


def test_run_python_small_completed_output_is_python_visible_output_only() -> None:
    text = format_tool_result_text(
        "run_python",
        {
            "ok": True,
            "execution_id": "exec-000123",
            "status": "completed",
            "stdout": "hello\n",
            "stderr": "",
            "result_text": "42",
            "output_text": "hello\nOut[7]: 42\n",
            "output_log": "python-output:exec-000123",
            "output_omitted": False,
            "output_omitted_reason": None,
            "output_line_limit": 300,
            "output_total_lines": 2,
            "error": None,
        },
    )

    assert text == "hello\nOut[7]: 42\n"
    assert "stdout:" not in text
    assert "result_text:" not in text
    assert "exec-" not in text
    assert "python-output:" not in text


def test_run_python_omitted_surfaces_are_natural_language_notices() -> None:
    running_text = format_tool_result_text(
        "run_python",
        {
            "ok": True,
            "execution_id": "exec-000002",
            "status": "running",
            "stdout": "",
            "stderr": "",
            "result_text": "",
            "output_log": "python-output:exec-000002",
            "output_omitted": True,
            "output_omitted_reason": "running",
            "output_line_limit": 300,
            "output_total_lines": 1,
            "error": None,
        },
    )
    large_text = format_tool_result_text(
        "wait_python",
        {
            "ok": True,
            "execution_id": "exec-000003",
            "status": "completed",
            "stdout": "",
            "stderr": "",
            "result_text": "",
            "output_log": "python-output:exec-000003",
            "output_omitted": True,
            "output_omitted_reason": "line_limit_exceeded",
            "output_line_limit": 300,
            "output_total_lines": 301,
            "error": None,
        },
    )

    assert running_text == "Python execution is still running. Use wait_python() to wait, python_status() to check its state, or read_python_output() to inspect available output."
    assert large_text == "Python output is available through read_python_output()."


def test_run_python_small_error_uses_traceback_body_only() -> None:
    traceback = "Traceback (most recent call last):\n  File \"<stdin>\", line 1, in <module>\nZeroDivisionError: division by zero\n"

    text = format_tool_result_text(
        "run_python",
        {
            "ok": False,
            "execution_id": "exec-000004",
            "status": "error",
            "stdout": "",
            "stderr": "",
            "result_text": "",
            "output_text": traceback,
            "output_log": "python-output:exec-000004",
            "output_omitted": False,
            "output_omitted_reason": None,
            "output_line_limit": 300,
            "output_total_lines": 3,
            "error": {"ename": "ZeroDivisionError", "evalue": "division by zero", "traceback_log": "python-output:exec-000004/traceback"},
        },
    )

    assert text == traceback
    assert "error:" not in text
    assert "python-output:" not in text


def test_read_and_search_success_surfaces_are_returned_without_footers() -> None:
    read_text = format_tool_result_text(
        "read_python_output",
        {
            "ok": True,
            "execution_id": "exec-000004",
            "output_log": "python-output:exec-000004/stdout",
            "stream": "stdout",
            "line_range": "299:301",
            "total_lines": 301,
            "returned_lines": 3,
            "omitted_before": 298,
            "omitted_after": 0,
            "text": "299 | line-298\n300 | line-299\n301 | line-300",
        },
    )
    search_text = format_tool_result_text(
        "search_python_output",
        {
            "ok": True,
            "execution_id": "exec-000004",
            "output_log": "python-output:exec-000004",
            "stream": "combined",
            "query": "match",
            "query_interpretation": "literal",
            "matched_lines": 2,
            "matches": 2,
            "context_before": 1,
            "context_after": 1,
            "total_lines": 5,
            "text": "C 2 | stderr-02 warn\nM 3 | stdout-03 beta-match\nC 4 | stdout-04 tail\nM 5 | stderr-05 err-match",
        },
    )

    assert read_text == "299 | line-298\n300 | line-299\n301 | line-300"
    assert search_text == "C 2 | stderr-02 warn\nM 3 | stdout-03 beta-match\nC 4 | stdout-04 tail\nM 5 | stderr-05 err-match"
    assert "python-output:" not in read_text + search_text


def test_read_output_returns_payload_without_a_footer() -> None:
    text = format_tool_result_text(
        "read_python_output",
        {
            "ok": True,
            "execution_id": "exec-000004",
            "output_log": "python-output:exec-000004/stdout",
            "stream": "stdout",
            "line_range": None,
            "show_line_numbers": False,
            "total_lines": 1,
            "returned_lines": 1,
            "omitted_before": 0,
            "omitted_after": 0,
            "text": "123 | payload",
        },
    )

    assert text == "123 | payload"


def test_empty_and_unmatched_output_surfaces_use_natural_language_notices() -> None:
    no_output = format_tool_result_text(
        "run_python",
        {
            "ok": True,
            "execution_id": "exec-000007",
            "status": "completed",
            "output_text": "",
            "output_log": "python-output:exec-000007",
            "output_omitted": False,
        },
    )
    no_lines = format_tool_result_text(
        "read_python_output",
        {"ok": True, "output_log": "python-output:exec-000007/stdout", "returned_lines": 0, "total_lines": 0, "text": ""},
    )
    no_matches = format_tool_result_text(
        "search_python_output",
        {"ok": True, "output_log": "python-output:exec-000007", "query": "missing", "query_interpretation": "literal", "matched_lines": 0, "matches": 0, "text": ""},
    )

    assert no_output == "Python execution completed without visible output."
    assert no_lines == "No output lines are available."
    assert no_matches == "No matching output lines were found."


def test_execution_status_error_and_killed_results_are_not_tool_failures() -> None:
    error_text = format_tool_result_text(
        "python_execution_status",
        {
            "ok": False,
            "execution_id": "exec-000005",
            "status": "error",
            "error": {"ename": "ZeroDivisionError", "evalue": "division by zero", "traceback_log": "python-output:exec-000005/traceback"},
            "output_log": "python-output:exec-000005",
            "submitted_at": 1777911000.0,
            "completed_at": 1777911001.0,
            "output_total_lines": 3,
        },
    )
    killed_text = format_tool_result_text(
        "python_execution_status",
        {
            "ok": False,
            "execution_id": "exec-000006",
            "status": "killed",
            "error": None,
            "output_log": "python-output:exec-000006",
            "submitted_at": 1777911002.0,
            "completed_at": 1777911003.0,
            "output_total_lines": 0,
        },
    )

    assert error_text.startswith("execution exec-000005: error\nerror: ZeroDivisionError: division by zero\ntraceback: python-output:exec-000005/traceback\nlog: python-output:exec-000005")
    assert killed_text.startswith("execution exec-000006: killed\nlog: python-output:exec-000006")
    assert "tool failed" not in error_text
    assert "tool failed" not in killed_text


def test_status_tools_keep_compact_status_oriented_text() -> None:
    status_text = format_tool_result_text(
        "python_status",
        {"ok": True, "workspace": "/tmp/ws", "python": "/tmp/ws/.venv/bin/python", "kernel_started": True, "kernel_pid": 101, "busy": True, "current_execution_id": "exec-000004", "execution_count": 1, "last_execution_id": "exec-000004"},
    )
    execution_text = format_tool_result_text(
        "python_execution_status",
        {
            "ok": True,
            "execution_id": "exec-000004",
            "status": "completed",
            "submitted_at": 1777911000.0,
            "completed_at": 1777911001.0,
            "output_log": "python-output:exec-000004",
            "output_total_lines": 301,
            "output_omitted_reason": "line_limit_exceeded",
        },
    )

    assert status_text == "kernel: busy\ncurrent_execution_id: exec-000004\nworkspace: /tmp/ws"
    assert execution_text == "execution exec-000004: completed\nlog: python-output:exec-000004\nsubmitted_at: 1777911000.0\ncompleted_at: 1777911001.0\noutput_total_lines: 301\noutput_omitted_reason: line_limit_exceeded"


def test_tool_failure_surface_uses_status_and_message() -> None:
    text = format_tool_result_text("read_python_output", {"ok": False, "status": "invalid_line_range", "message": "line_range must use start:stop"})

    assert text == "invalid_line_range: line_range must use start:stop"
