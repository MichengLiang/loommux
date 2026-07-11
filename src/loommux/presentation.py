from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def format_tool_result_text(tool_name: str, status: Mapping[str, Any]) -> str:
    if _is_tool_failure(tool_name, status):
        return _failure_surface(status)
    if tool_name in {"run_python", "wait_python"}:
        return _execution_output_surface(status)
    if tool_name == "read_python_output":
        return _read_output_surface(status)
    if tool_name == "search_python_output":
        return _search_output_surface(status)
    if tool_name == "python_status":
        return _python_status_surface(status)
    if tool_name == "python_execution_status":
        return _python_execution_status_surface(status)
    if tool_name == "interrupt_python":
        return _interrupt_surface(status)
    if tool_name == "reset_python":
        return _reset_surface(status)
    return _generic_surface(status)


def _is_tool_failure(tool_name: str, status: Mapping[str, Any]) -> bool:
    if status.get("ok") is not False:
        return False
    if tool_name == "python_execution_status" and status.get("execution_id") is not None:
        return False
    return not (tool_name in {"run_python", "wait_python"} and status.get("status") == "error")


def _failure_surface(status: Mapping[str, Any]) -> str:
    status_value = _optional_string(status.get("status")) or "error"
    message = _optional_string(status.get("message")) or "tool failed"
    return f"{status_value}: {message}"


def _execution_output_surface(status: Mapping[str, Any]) -> str:
    if status.get("output_omitted") is True:
        return _omitted_execution_notice(status)

    output_text = _optional_string(status.get("output_text")) or ""
    if output_text:
        return output_text
    if status.get("status") == "error":
        return "Python execution failed without visible output."
    return "Python execution completed without visible output."



def _omitted_execution_notice(status: Mapping[str, Any]) -> str:
    reason = _optional_string(status.get("output_omitted_reason")) or "unknown"
    if reason == "running":
        return "Python execution is still running. Use wait_python() to wait, python_status() to check its state, or read_python_output() to inspect available output."
    if reason == "line_limit_exceeded":
        return "Python output is available through read_python_output()."
    return "Python output is not available in this response."


def _read_output_surface(status: Mapping[str, Any]) -> str:
    text = _optional_string(status.get("text")) or ""
    returned_lines = _optional_int(status.get("returned_lines"))
    if returned_lines == 0 or not text:
        return "No output lines are available."
    return text


def _search_output_surface(status: Mapping[str, Any]) -> str:
    matched_lines = _optional_int(status.get("matched_lines"))
    if matched_lines == 0:
        return "No matching output lines were found."
    text = _optional_string(status.get("text")) or ""
    return text


def _python_status_surface(status: Mapping[str, Any]) -> str:
    kernel_state = "busy" if status.get("busy") is True else "idle" if status.get("kernel_started") is True else "not_started"
    lines = [f"kernel: {kernel_state}"]
    if kernel_state == "busy":
        lines.append(f"current_execution_id: {_format_compact_value(status.get('current_execution_id'))}")
    lines.append(f"workspace: {_format_compact_value(status.get('workspace'))}")
    if kernel_state != "busy":
        lines.append(f"python: {_format_compact_value(status.get('python'))}")
        lines.append(f"last_execution_id: {_format_compact_value(status.get('last_execution_id'))}")
    return "\n".join(lines)


def _python_execution_status_surface(status: Mapping[str, Any]) -> str:
    execution_id = _optional_string(status.get("execution_id")) or "unknown"
    status_value = _optional_string(status.get("status")) or "unknown"
    lines = [f"execution {execution_id}: {status_value}"]
    error = status.get("error")
    if isinstance(error, Mapping) and error:
        ename = _optional_string(error.get("ename")) or "Error"
        evalue = _optional_string(error.get("evalue")) or ""
        lines.append(f"error: {ename}: {evalue}" if evalue else f"error: {ename}")
        traceback_log = _optional_string(error.get("traceback_log"))
        if traceback_log is not None:
            lines.append(f"traceback: {traceback_log}")
    output_log = _optional_string(status.get("output_log"))
    if output_log is not None:
        lines.append(f"log: {output_log}")
    for key in ("submitted_at", "completed_at", "output_total_lines", "output_omitted_reason"):
        value = status.get(key)
        if value is not None:
            lines.append(f"{key}: {_format_compact_value(value)}")
    return "\n".join(lines)


def _interrupt_surface(status: Mapping[str, Any]) -> str:
    if status.get("status") == "interrupt_sent":
        return f"中断：已向 execution {_format_compact_value(status.get('execution_id'))} 发送 interrupt。"
    if status.get("status") == "idle":
        return "中断：kernel 当前空闲，无需 interrupt。"
    return _generic_surface(status)


def _reset_surface(status: Mapping[str, Any]) -> str:
    if status.get("status") == "restarted":
        return "重置：kernel 已重启。"
    return _generic_surface(status)


def _generic_surface(status: Mapping[str, Any]) -> str:
    status_value = _optional_string(status.get("status"))
    if status_value is not None:
        return status_value
    if status.get("ok") is True:
        return "ok"
    return json.dumps(status, ensure_ascii=False, default=str)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _format_compact_value(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)
