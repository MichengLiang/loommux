from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

PRIMARY_KEY_ORDER = (
    "ok",
    "status",
    "execution_id",
    "current_execution_id",
    "workspace",
    "python",
    "kernel_started",
    "kernel_pid",
    "busy",
    "execution_count",
    "last_execution_id",
    "message",
)

KERNEL_KEY_ORDER = (
    "busy",
    "kernel_pid",
    "execution_count",
)

CONTENT_BLOCK_ORDER = (
    "result_text",
    "stdout",
    "stderr",
)

SPECIAL_KEYS = {*CONTENT_BLOCK_ORDER, "error", "kernel"}


def format_tool_result_text(tool_name: str, status: Mapping[str, Any]) -> str:
    sections = [_conclusion(tool_name, status), _key_values(status)]
    kernel = status.get("kernel")
    if isinstance(kernel, Mapping):
        sections.append(_mapping_section("kernel", kernel, KERNEL_KEY_ORDER))
    sections.extend(_content_sections(status))
    error = status.get("error")
    if error:
        sections.append(_error_section(error))
    return "\n\n".join(section for section in sections if section)


def _conclusion(tool_name: str, status: Mapping[str, Any]) -> str:
    status_value = status.get("status")
    execution_id = _optional_string(status.get("execution_id"))
    current_execution_id = _optional_string(status.get("current_execution_id"))

    if status_value == "busy":
        active_id = current_execution_id or "当前 execution"
        return f"前置状态：kernel 正在执行 {active_id}，未提交新代码。"
    if status_value == "workspace_not_set":
        return "前置状态：workspace 尚未设置。"
    if status_value == "kernel_not_started":
        return "前置状态：kernel 未启动。"
    if status_value == "execution_not_found":
        return "前置状态：未找到 execution。"
    if status_value == "invalid_timeout":
        return "前置状态：timeout_seconds 无效。"
    if status_value == "invalid_code":
        return "前置状态：code 无效。"

    if tool_name == "python_status":
        return _status_conclusion(status)
    if tool_name == "set_workspace":
        return _set_workspace_conclusion(status)
    if tool_name in {"run_python", "wait_python", "read_python_output"}:
        return _execution_conclusion(status_value, execution_id)
    if tool_name == "interrupt_python":
        return _interrupt_conclusion(status_value, execution_id)
    if tool_name == "reset_python":
        return _reset_conclusion(status_value)
    return _generic_conclusion(status)


def _status_conclusion(status: Mapping[str, Any]) -> str:
    if status.get("workspace") is None and status.get("kernel_started") is False:
        return "状态：workspace 未设置，kernel 未启动。"
    current_execution_id = _optional_string(status.get("current_execution_id"))
    if status.get("busy") is True:
        active_id = current_execution_id or "当前 execution"
        return f"状态：kernel 正在执行 {active_id}。"
    if status.get("kernel_started") is True:
        return "状态：kernel 已启动，当前空闲。"
    return "状态：kernel 未启动。"


def _set_workspace_conclusion(status: Mapping[str, Any]) -> str:
    if status.get("ok") is True:
        return "工作区：workspace 已设置，kernel 已启动。"
    status_value = _optional_string(status.get("status")) or "unknown"
    return f"前置状态：workspace 设置失败（{status_value}）。"


def _execution_conclusion(status_value: object, execution_id: str | None) -> str:
    execution_label = execution_id or "当前 execution"
    if status_value == "completed":
        return f"完成：execution {execution_label} 已完成。"
    if status_value == "running":
        return f"运行中：execution {execution_label} 仍在执行。"
    if status_value == "error":
        return f"错误：execution {execution_label} 执行失败。"
    if status_value == "interrupted":
        return f"中断：execution {execution_label} 已结束。"
    if status_value == "killed":
        return f"终止：execution {execution_label} 已被 reset 终止。"
    return _generic_conclusion({"status": status_value, "execution_id": execution_id})


def _interrupt_conclusion(status_value: object, execution_id: str | None) -> str:
    if status_value == "interrupt_sent":
        execution_label = execution_id or "当前 execution"
        return f"中断：已向 execution {execution_label} 发送 interrupt。"
    if status_value == "idle":
        return "中断：kernel 当前空闲，无需 interrupt。"
    return _generic_conclusion({"status": status_value})


def _reset_conclusion(status_value: object) -> str:
    if status_value == "restarted":
        return "重置：kernel 已重启。"
    return _generic_conclusion({"status": status_value})


def _generic_conclusion(status: Mapping[str, Any]) -> str:
    status_value = status.get("status")
    ok = status.get("ok")
    if status_value is not None:
        return f"状态：{_format_value(status_value)}。"
    if ok is True:
        return "状态：操作成功。"
    if ok is False:
        return "状态：操作失败。"
    return "状态：已返回。"


def _key_values(status: Mapping[str, Any]) -> str:
    lines = ["关键状态:"]
    for key in _ordered_keys(status, PRIMARY_KEY_ORDER, SPECIAL_KEYS):
        lines.append(f"- {key}: {_format_value(status[key])}")
    return "\n".join(lines)


def _mapping_section(title: str, values: Mapping[str, Any], preferred_order: tuple[str, ...]) -> str:
    lines = [f"{title}:"]
    for key in _ordered_keys(values, preferred_order, frozenset()):
        lines.append(f"- {key}: {_format_value(values[key])}")
    return "\n".join(lines)


def _content_sections(status: Mapping[str, Any]) -> list[str]:
    sections: list[str] = []
    for key in CONTENT_BLOCK_ORDER:
        value = status.get(key)
        if value:
            sections.append(f"{key}:\n{_format_block_value(value)}")
    return sections


def _error_section(error: object) -> str:
    if not isinstance(error, Mapping):
        return f"error:\n{_format_block_value(error)}"

    lines = ["error:"]
    if "ename" in error:
        lines.append(f"- ename: {_format_value(error['ename'])}")
    if "evalue" in error:
        lines.append(f"- evalue: {_format_value(error['evalue'])}")
    traceback = error.get("traceback")
    if traceback:
        lines.append("traceback:")
        lines.append(_format_traceback(traceback))
    return "\n".join(lines)


def _format_traceback(traceback: object) -> str:
    if isinstance(traceback, list):
        return "\n".join(str(line) for line in traceback)
    return _format_block_value(traceback)


def _ordered_keys(values: Mapping[str, Any], preferred_order: tuple[str, ...], excluded: set[str] | frozenset[str]) -> list[str]:
    preferred = [key for key in preferred_order if key in values and key not in excluded]
    remaining = sorted(key for key in values if key not in excluded and key not in preferred)
    return [*preferred, *remaining]


def _format_value(value: object) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


def _format_block_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return _format_value(value)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
