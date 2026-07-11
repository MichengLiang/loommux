from __future__ import annotations

from loommux.presentation import format_tool_result_text


def test_completed_result_uses_public_out_label_and_assignment_has_completion_anchor() -> None:
    result = {"ok": True, "execution": 5, "status": "completed", "result_text": "42", "output_text": "Out[5]: 42\n", "output_omitted": False}
    assignment = {"ok": True, "execution": 6, "status": "completed", "result_text": "", "output_text": "printed\n", "output_omitted": False}

    assert format_tool_result_text("run_python", result) == "Out[5]: 42\n"
    assert format_tool_result_text("run_python", assignment) == "printed\nExecution 6 completed without a display result."


def test_execution_states_name_the_integer_coordinate() -> None:
    running = {"ok": True, "execution": 5, "status": "running", "output_omitted_reason": "running"}
    large = {"ok": True, "execution": 5, "status": "completed", "output_omitted_reason": "line_limit_exceeded", "output_line_limit": 300}
    error = {"ok": False, "execution": 5, "status": "error", "error": {"ename": "ZeroDivisionError", "evalue": "division by zero"}}
    killed = {"ok": False, "execution": 5, "status": "killed"}

    assert format_tool_result_text("run_python", running) == "Python execution 5 is still running. Use wait_python() or read_python_output()."
    assert format_tool_result_text("wait_python", large) == "Python execution 5 produced more than 300 lines. Use read_python_output()."
    assert format_tool_result_text("run_python", error) == "Python execution 5 failed with ZeroDivisionError: division by zero."
    assert format_tool_result_text("wait_python", killed) == "Python execution 5 was killed by reset_python()."


def test_marked_terminal_execution_renders_its_complete_combined_output() -> None:
    error = {
        "ok": False,
        "execution": 5,
        "status": "error",
        "full_output_requested": True,
        "output_text": "before failure\nTraceback...\n",
        "output_omitted": False,
    }
    killed = {
        "ok": False,
        "execution": 6,
        "status": "killed",
        "full_output_requested": True,
        "output_text": "before reset\n",
        "output_omitted": False,
    }

    assert format_tool_result_text("run_python", error) == "before failure\nTraceback...\n"
    assert format_tool_result_text("wait_python", killed) == "before reset\n"


def test_read_search_and_status_surfaces_are_output_oriented() -> None:
    assert format_tool_result_text("read_python_output", {"ok": True, "returned_lines": 1, "text": "1 | payload"}) == "1 | payload"
    assert format_tool_result_text("read_python_output", {"ok": True, "returned_lines": 0, "text": ""}) == "No output lines are available."
    assert format_tool_result_text("search_python_output", {"ok": True, "matched_lines": 0}) == "No matching output lines were found."
    assert format_tool_result_text("python_status", {"ok": True, "kernel_started": True, "busy": False, "recent_execution": 5, "workspace": "/tmp/ws"}) == "kernel: idle\nrecent_execution: 5\nworkspace: /tmp/ws"
    assert format_tool_result_text("python_execution_status", {"ok": True, "execution": 5, "status": "completed", "output_total_lines": 2}).startswith("execution 5: completed")


def test_tool_failure_surface_remains_concise() -> None:
    assert format_tool_result_text("read_python_output", {"ok": False, "status": "invalid_stream", "message": "bad stream"}) == "invalid_stream: bad stream"


def test_presentation_handles_remaining_lifecycle_and_status_cases() -> None:
    assert format_tool_result_text("interrupt_python", {"ok": True, "status": "interrupt_sent", "execution": 7}) == "Interrupt sent to Python execution 7."
    assert format_tool_result_text("interrupt_python", {"ok": True, "status": "idle"}) == "Python kernel is idle."
    assert format_tool_result_text("reset_python", {"ok": True, "status": "restarted"}).startswith("Python kernel restarted")
    assert format_tool_result_text("python_execution_status", {"ok": False, "execution": 8, "status": "error", "error": {"ename": "NameError"}}).startswith("execution 8: error\nPython execution 8 failed with NameError")
