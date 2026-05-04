from __future__ import annotations

from loommux.presentation import format_tool_result_text


def test_run_python_small_completed_output_is_body_first_with_one_line_footer() -> None:
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

    assert text == "hello\nOut[7]: 42\n\n[exec-000123 completed | log: python-output:exec-000123]"
    assert "stdout:" not in text
    assert "result_text:" not in text
    assert "logs" not in text


def test_run_python_omitted_surfaces_are_single_footer_lines_without_empty_blocks() -> None:
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

    assert running_text == "[exec-000002 running | output omitted: running | 1 line available | log: python-output:exec-000002]"
    assert large_text == "[exec-000003 completed | output omitted: 301 lines > 300 | log: python-output:exec-000003]"
    assert "stdout:" not in running_text
    assert "stderr:" not in large_text
    assert "logs" not in running_text + large_text


def test_run_python_small_error_uses_traceback_body_but_summary_error_footer() -> None:
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

    assert text == f"{traceback}\n[exec-000004 error | traceback: python-output:exec-000004/traceback | log: python-output:exec-000004]"
    assert "error:" not in text


def test_read_and_search_success_surfaces_are_text_first_with_one_line_footer() -> None:
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

    assert read_text == "299 | line-298\n300 | line-299\n301 | line-300\n\n[python-output:exec-000004/stdout | lines 299-301 of 301]"
    assert search_text.startswith("C 2 | stderr-02 warn\nM 3 | stdout-03 beta-match")
    assert search_text.endswith("[python-output:exec-000004 | query: match (literal) | 2 matched lines, 2 matches]")
    assert "状态：已返回。" not in read_text
    assert "状态：操作成功。" not in search_text


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
