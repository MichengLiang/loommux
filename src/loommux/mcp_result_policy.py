from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from fastmcp.tools import ToolResult

from loommux.presentation import format_tool_result_text

ResultChannelPolicy = Literal["dual_channel", "content_only"]


def make_tool_result(tool_name: str, raw_status: Mapping[str, Any], policy: ResultChannelPolicy) -> ToolResult:
    text = format_tool_result_text(tool_name, raw_status)
    if policy == "dual_channel":
        return ToolResult(content=text, structured_content=dict(raw_status))
    if policy == "content_only":
        return ToolResult(content=text)
    raise ValueError(f"unknown result channel policy: {policy}")
