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
    execution_id = _optional_string(status.get("execution_id")) or "unknown"
    status_value = _optional_string(status.get("status")) or "unknown"
    output_log = _optional_string(status.get("output_log")) or f"python-output:{execution_id}"
    if status.get("output_omitted") is True:
        return _omitted_execution_footer(execution_id, status_value, output_log, status)

    output_text = _optional_string(status.get("output_text")) or ""
    if not output_text:
        return f"[{execution_id} {status_value} | no output | log: {output_log}]"

    footer = _execution_footer(execution_id, status_value, output_log, status)
    return f"{output_text}\n{footer}" if output_text.endswith("\n") else f"{output_text}\n\n{footer}"


def _omitted_execution_footer(execution_id: str, status_value: str, output_log: str, status: Mapping[str, Any]) -> str:
    reason = _optional_string(status.get("output_omitted_reason")) or "unknown"
    total_lines = _optional_int(status.get("output_total_lines"))
    line_limit = _optional_int_or_none(status.get("output_line_limit"))
    if reason == "running":
        line_word = "line" if total_lines == 1 else "lines"
        return f"[{execution_id} {status_value} | output omitted: running | {total_lines} {line_word} available | log: {output_log}]"
    if reason == "line_limit_exceeded" and line_limit is not None:
        return f"[{execution_id} {status_value} | output omitted: {total_lines} lines > {line_limit} | log: {output_log}]"
    return f"[{execution_id} {status_value} | output omitted: {reason} | log: {output_log}]"


def _execution_footer(execution_id: str, status_value: str, output_log: str, status: Mapping[str, Any]) -> str:
    if status_value == "error":
        error = status.get("error")
        if isinstance(error, Mapping):
            traceback_log = _optional_string(error.get("traceback_log"))
            if traceback_log is not None:
                return f"[{execution_id} error | traceback: {traceback_log} | log: {output_log}]"
    return f"[{execution_id} {status_value} | log: {output_log}]"


def _read_output_surface(status: Mapping[str, Any]) -> str:
    output_log = _optional_string(status.get("output_log")) or "python-output:unknown"
    text = _optional_string(status.get("text")) or ""
    returned_lines = _optional_int(status.get("returned_lines"))
    total_lines = _optional_int(status.get("total_lines"))
    if returned_lines == 0 or not text:
        return f"[{output_log} | no lines]"
    footer = _read_footer(output_log, text, returned_lines, total_lines, status)
    return f"{text}\n\n{footer}"


def _read_footer(output_log: str, text: str, returned_lines: int, total_lines: int, status: Mapping[str, Any]) -> str:
    omitted_before = _optional_int(status.get("omitted_before"))
    omitted_after = _optional_int(status.get("omitted_after"))
    first_line = text.splitlines()[0] if text else ""
    show_line_numbers = status.get("show_line_numbers")
    has_line_numbers = show_line_numbers if isinstance(show_line_numbers, bool) else first_line.split(" | ", 1)[0].isdigit()
    if has_line_numbers:
        start = omitted_before + 1
        stop = omitted_before + returned_lines
        return f"[{output_log} | lines {start}-{stop} of {total_lines}]"

    parts = [f"{returned_lines} {'line' if returned_lines == 1 else 'lines'} of {total_lines}"]
    if omitted_before:
        parts.append(f"omitted_before={omitted_before}")
    if omitted_after:
        parts.append(f"omitted_after={omitted_after}")
    return f"[{output_log} | {' | '.join(parts)}]"


def _search_output_surface(status: Mapping[str, Any]) -> str:
    output_log = _optional_string(status.get("output_log")) or "python-output:unknown"
    query = _optional_string(status.get("query")) or ""
    interpretation = _optional_string(status.get("query_interpretation")) or "unknown"
    matched_lines = _optional_int(status.get("matched_lines"))
    matches = _optional_int(status.get("matches"))
    if matched_lines == 0:
        return f"[{output_log} | query: {query} ({interpretation}) | no matches]"
    text = _optional_string(status.get("text")) or ""
    line_word = "line" if matched_lines == 1 else "lines"
    match_word = "match" if matches == 1 else "matches"
    footer = f"[{output_log} | query: {query} ({interpretation}) | {matched_lines} matched {line_word}, {matches} {match_word}]"
    return f"{text}\n\n{footer}" if text else footer


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


def _optional_int_or_none(value: object) -> int | None:
    if value is None:
        return None
    return _optional_int(value)


def _format_compact_value(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)
