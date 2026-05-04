from __future__ import annotations

from loommux.presentation import format_tool_result_text


def test_execution_formatter_keeps_stable_key_order_and_output_block_order() -> None:
    text = format_tool_result_text(
        "wait_python",
        {
            "ok": True,
            "execution_id": "exec-000123",
            "status": "completed",
            "stdout": "stdout-a\nstdout-b\n",
            "stderr": "stderr-a\n",
            "result_text": "42",
            "error": None,
            "kernel": {"busy": False, "kernel_pid": 456, "execution_count": 7},
        },
    )

    assert text.splitlines()[0] == "完成：execution exec-000123 已完成。"
    assert text.index("- ok: true") < text.index("- status: completed") < text.index("- execution_id: exec-000123")
    assert text.index("result_text:") < text.index("stdout:") < text.index("stderr:")
    assert "stdout-a\nstdout-b\n" in text
    assert "stderr-a\n" in text
    assert "42" in text


def test_error_formatter_includes_error_fields_and_traceback_without_truncation() -> None:
    traceback = [
        "Traceback (most recent call last):",
        "  File \"<ipython-input-1>\", line 1, in <module>",
        "ZeroDivisionError: division by zero",
    ]

    text = format_tool_result_text(
        "run_python",
        {
            "ok": False,
            "execution_id": "exec-000001",
            "status": "error",
            "stdout": "",
            "stderr": "",
            "result_text": "",
            "error": {"ename": "ZeroDivisionError", "evalue": "division by zero", "traceback": traceback},
        },
    )

    assert text.splitlines()[0] == "错误：execution exec-000001 执行失败。"
    assert "- ename: ZeroDivisionError" in text
    assert "- evalue: division by zero" in text
    assert "traceback:\n" + "\n".join(traceback) in text


def test_precondition_formatter_front_loads_busy_and_not_found_states() -> None:
    busy_text = format_tool_result_text(
        "run_python",
        {"ok": False, "status": "busy", "current_execution_id": "exec-000002", "message": "kernel is already executing code"},
    )
    not_found_text = format_tool_result_text(
        "wait_python",
        {"ok": False, "status": "execution_not_found", "message": "execution was not found", "kernel": {"busy": False, "kernel_pid": 123}},
    )

    assert busy_text.splitlines()[0] == "前置状态：kernel 正在执行 exec-000002，未提交新代码。"
    assert not_found_text.splitlines()[0] == "前置状态：未找到 execution。"
    assert "- current_execution_id: exec-000002" in busy_text
    assert "- status: execution_not_found" in not_found_text


def test_control_formatter_covers_interrupt_and_reset() -> None:
    interrupt_text = format_tool_result_text("interrupt_python", {"ok": True, "status": "interrupt_sent", "execution_id": "exec-000003", "kernel_pid": 789})
    reset_text = format_tool_result_text(
        "reset_python",
        {"ok": True, "status": "restarted", "workspace": "/tmp/ws", "python": "/tmp/ws/.venv/bin/python", "kernel_started": True, "kernel_pid": 790, "busy": False, "current_execution_id": None, "execution_count": 0},
    )

    assert interrupt_text.splitlines()[0] == "中断：已向 execution exec-000003 发送 interrupt。"
    assert reset_text.splitlines()[0] == "重置：kernel 已重启。"


def test_workspace_and_status_formatter_cover_workspace_tools() -> None:
    workspace_text = format_tool_result_text(
        "set_workspace",
        {"ok": True, "workspace": "/tmp/ws", "python": "/tmp/ws/.venv/bin/python", "kernel_started": True, "kernel_pid": 101, "busy": False, "current_execution_id": None, "execution_count": 0},
    )
    missing_workspace_text = format_tool_result_text(
        "set_workspace",
        {"ok": False, "status": "python_not_found", "message": "workspace Python does not exist", "workspace": "/tmp/ws", "python": "/tmp/ws/.venv/bin/python"},
    )
    busy_status_text = format_tool_result_text(
        "python_status",
        {"ok": True, "workspace": "/tmp/ws", "python": "/tmp/ws/.venv/bin/python", "kernel_started": True, "kernel_pid": 101, "busy": True, "current_execution_id": "exec-000004", "execution_count": 1, "last_execution_id": "exec-000004"},
    )

    assert workspace_text.splitlines()[0] == "工作区：workspace 已设置，kernel 已启动。"
    assert missing_workspace_text.splitlines()[0] == "前置状态：workspace 设置失败（python_not_found）。"
    assert busy_status_text.splitlines()[0] == "状态：kernel 正在执行 exec-000004。"
