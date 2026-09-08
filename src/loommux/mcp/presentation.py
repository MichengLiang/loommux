"""Render session results for MCP callers and language-model readers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def format_tool_result_text(tool_name: str, status: Mapping[str, Any]) -> str:
    """Render one session status into the text surface intended for MCP."""

    if _is_tool_failure(tool_name, status):
        return _failure_surface(status)
    if tool_name in {"run_cell", "wait"}:
        return _execution_surface(status)
    if tool_name == "read_output":
        return _read_output_surface(status)
    if tool_name == "search_output":
        return _search_output_surface(status)
    if tool_name == "status":
        return _python_status_surface(status)
    if tool_name == "execution_status":
        return _execution_status_surface(status)
    if tool_name == "interrupt":
        return _interrupt_surface(status)
    if tool_name == "restart":
        return "Python kernel restarted; the session execution sequence is preserved." if status.get("status") == "restarted" else _generic_surface(status)
    return _generic_surface(status)


def _is_tool_failure(tool_name: str, status: Mapping[str, Any]) -> bool:
    if status.get("ok") is not False:
        return False
    # Python errors and killed records are execution states, not MCP transport failures.
    return not (tool_name in {"run_cell", "wait", "execution_status"} and isinstance(status.get("execution"), int))


def _execution_surface(status: Mapping[str, Any]) -> str:
    execution = _execution_number(status)
    state = str(status.get("status", "unknown"))
    output = _optional_string(status.get("output_text")) or ""
    if state == "running":
        return _with_execution_input(execution, "Running: use wait() for completion, read_output() for collected output, or search_output() to locate text.")
    if state == "error":
        return _with_execution_input(execution, output or _error_detail(status))
    if state == "interrupted":
        return _with_execution_input(
            execution,
            output or "Interrupted: use read_output() to read its output or search_output() to locate text.",
        )
    if state == "killed":
        body = _append_control_line(output, "Killed: restart() stopped this execution.")
        return _with_execution_input(execution, body)
    if status.get("output_omitted_reason") == "token_limit_exceeded":
        return _with_execution_input(execution, format_output_token_limit_notice(status))
    return _with_execution_input(execution, output)


def format_output_token_limit_notice(status: Mapping[str, Any]) -> str:
    """Describe an omitted combined log without exposing tokenizer internals."""
    total_lines = _number(status.get("output_total_lines"))
    total_characters = _number(status.get("output_total_characters"))
    total_bytes = _number(status.get("output_total_utf8_bytes"))
    return "\n".join(
        (
            f"Output omitted: {total_lines:,} lines, {total_characters:,} characters, {_format_binary_size(total_bytes)}; exceeds the 5,000-token limit. Use read_output() to read all lines or search_output() to locate text.",
            "对于明确需要完整阅读的信息，请标注 ``# loommux: --full-output``。",
        )
    )


def _format_binary_size(byte_count: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = float(byte_count)
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{byte_count:,} B"
    rendered = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{rendered} {units[unit_index]}"


def _error_sentence(execution: str, status: Mapping[str, Any]) -> str:
    error = status.get("error")
    if isinstance(error, Mapping):
        name = str(error.get("ename") or "Error")
        value = str(error.get("evalue") or "")
        return f"Python execution {execution} failed with {name}{': ' + value if value else ''}."
    return f"Python execution {execution} failed. Use read_output() to inspect its traceback or search_output() to locate text."


def _error_detail(status: Mapping[str, Any]) -> str:
    error = status.get("error")
    if isinstance(error, Mapping):
        name = str(error.get("ename") or "Error")
        value = str(error.get("evalue") or "")
        return f"Error: {name}{': ' + value if value else ''}"
    return "Error: use read_output() to inspect its traceback or search_output() to locate text."


def _with_execution_input(execution: str, body: str) -> str:
    """Project the accepted MCP cell as IPython's input-side coordinate."""
    header = format_execution_input(execution)
    return header if not body else f"{header}\n{body}"


def format_execution_input(execution: int | str) -> str:
    """Render the stable loommux execution coordinate used by every result mode."""
    return f"In [{execution}]:"


def _append_control_line(output: str, control: str) -> str:
    """Keep a killed state explicit when prior Python text cannot prove termination."""
    return control if not output else f"{output.rstrip()}\n{control}"


def _read_output_surface(status: Mapping[str, Any]) -> str:
    text = _optional_string(status.get("text")) or ""
    return text if text and _number(status.get("returned_lines")) else "No output lines are available."


def _search_output_surface(status: Mapping[str, Any]) -> str:
    return "No matching output lines were found." if _number(status.get("matched_lines")) == 0 else (_optional_string(status.get("text")) or "No matching output lines were found.")


def _python_status_surface(status: Mapping[str, Any]) -> str:
    state = "busy" if status.get("busy") is True else "idle" if status.get("kernel_started") is True else "not_started"
    lines = [f"kernel: {state}"]
    if state == "busy":
        lines.append(f"current_execution: {_compact(status.get('current_execution'))}")
    else:
        lines.append(f"recent_execution: {_compact(status.get('recent_execution'))}")
    lines.append(f"workspace: {_compact(status.get('workspace'))}")
    lines.append(f"workspace_resolution: {_compact(status.get('workspace_resolution'))}")
    return "\n".join(lines)


def _execution_status_surface(status: Mapping[str, Any]) -> str:
    execution = _execution_number(status)
    lines = [f"execution {execution}: {status.get('status', 'unknown')}"]
    error = status.get("error")
    if isinstance(error, Mapping):
        lines.append(_error_sentence(execution, status).removesuffix("."))
    for key in ("submitted_at", "updated_at", "completed_at", "kernel_pid", "execution_count_at_submit", "output_total_lines", "output_omitted_reason"):
        if status.get(key) is not None:
            lines.append(f"{key}: {_compact(status[key])}")
    return "\n".join(lines)


def _interrupt_surface(status: Mapping[str, Any]) -> str:
    if status.get("status") == "interrupt_sent":
        return f"Interrupt sent to Python execution {_execution_number(status)}."
    if status.get("status") == "idle":
        return "Python kernel is idle."
    return _generic_surface(status)


def _failure_surface(status: Mapping[str, Any]) -> str:
    return f"{status.get('status', 'error')}: {status.get('message', 'tool failed')}"


def _generic_surface(status: Mapping[str, Any]) -> str:
    return str(status["status"]) if status.get("status") is not None else "ok" if status.get("ok") is True else json.dumps(status, ensure_ascii=False, default=str)


def _execution_number(status: Mapping[str, Any]) -> str:
    return str(status.get("execution", "unknown"))


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None


def _number(value: object) -> int:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def _compact(value: object) -> str:
    return "null" if value is None else value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
