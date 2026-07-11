from __future__ import annotations

import stat
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.tools import ToolResult

from loommux.mcp_ipython_content_server import create_mcp as create_content_mcp
from loommux.mcp_ipython_server import create_mcp as create_standard_mcp
from loommux.mcp_result_policy import make_tool_result

EXPECTED_TOOLS = {
    "run_python",
    "python_status",
    "python_execution_status",
    "read_python_output",
    "search_python_output",
    "wait_python",
    "interrupt_python",
    "reset_python",
}


@pytest.fixture
async def content_client(valid_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Client[Any]]:
    monkeypatch.chdir(valid_workspace)
    async with Client(create_content_mcp()) as mcp_client:
        yield mcp_client


@pytest.fixture
def valid_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    python_path = workspace / ".venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text(f'#!/bin/sh\nexec {sys.executable} "$@"\n')
    python_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return workspace


def result_text(result: Any) -> str:
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    return result.content[0].text


def assert_content_only_result(result: Any) -> str:
    assert result.structured_content is None
    assert result.data is None
    assert result.meta is None or result.meta == {}
    return result_text(result)


def test_make_tool_result_exposes_dual_channel_or_content_only() -> None:
    raw_status = {"ok": True, "workspace": None, "python": None, "kernel_started": False, "kernel_pid": None, "busy": False, "current_execution_id": None, "execution_count": 0, "last_execution_id": None}

    dual = make_tool_result("python_status", raw_status, "dual_channel")
    content_only = make_tool_result("python_status", raw_status, "content_only")

    assert isinstance(dual, ToolResult)
    assert dual.structured_content == raw_status
    assert dual.structured_content is not raw_status
    assert content_only.structured_content is None
    assert dual.content == content_only.content
    assert len(dual.content) == 1
    assert dual.content[0].type == "text"
    assert dual.content[0].text == "kernel: not_started\nworkspace: null\npython: null\nlast_execution_id: null"


async def test_content_only_tools_match_standard_server_and_declare_no_output_schema(content_client: Client[Any]) -> None:
    async with Client(create_standard_mcp()) as standard_client:
        standard_tools = await standard_client.list_tools()
    content_tools = await content_client.list_tools()

    standard_names = {tool.name for tool in standard_tools}
    content_names = {tool.name for tool in content_tools}

    assert standard_names == content_names == EXPECTED_TOOLS
    for tool in content_tools:
        assert tool.inputSchema is not None
        assert tool.outputSchema is None


async def test_content_only_python_status_returns_pretty_text_without_structured_channels(content_client: Client[Any], valid_workspace: Path) -> None:
    result = await content_client.call_tool("python_status", {})

    text = assert_content_only_result(result)
    assert text.startswith(f"kernel: idle\nworkspace: {valid_workspace}\npython: ")


async def test_content_only_run_python_preserves_output_first_pretty_text(content_client: Client[Any], valid_workspace: Path) -> None:
    result = await content_client.call_tool("run_python", {"freeform": "print('hello-content-only')\n42"})
    text = assert_content_only_result(result)

    assert text.startswith("hello-content-only\nOut[")
    assert "42" in text
    assert "exec-" not in text
    assert "python-output:" not in text
    assert '"status"' not in text
    assert "'status'" not in text
    assert "structuredContent" not in text


async def test_content_only_error_execution_has_traceback_text_but_no_structured_channels(content_client: Client[Any], valid_workspace: Path) -> None:
    result = await content_client.call_tool("run_python", {"freeform": "1 / 0"})
    text = assert_content_only_result(result)

    assert "ZeroDivisionError: division by zero" in text
    assert "exec-" not in text
    assert "python-output:" not in text
    assert '"error"' not in text


async def test_content_only_running_execution_omits_partial_body_and_structured_channels(content_client: Client[Any], valid_workspace: Path) -> None:
    result = await content_client.call_tool("run_python", {"freeform": "# loommux: timeout_seconds=0.1\nimport time\nprint('partial-content-only', flush=True)\ntime.sleep(1)"})

    assert assert_content_only_result(result) == "Python execution is still running. Use wait_python() to wait, python_status() to check its state, or read_python_output() to inspect available output."

    await content_client.call_tool("wait_python", {"execution_id": "exec-000001", "timeout_seconds": 5})


async def test_content_only_all_tools_return_no_structured_channels(content_client: Client[Any], valid_workspace: Path) -> None:
    calls = [
        ("python_status", {}),
        ("run_python", {"freeform": "print('all-tools-content-only')"}),
        ("python_execution_status", {"execution_id": "exec-000001"}),
        ("read_python_output", {"output_log": "python-output:exec-000001", "stream": "stdout"}),
        ("search_python_output", {"output_log": "python-output:exec-000001", "stream": "stdout", "query": "all-tools", "query_mode": "literal"}),
        ("wait_python", {"execution_id": "exec-000001", "timeout_seconds": 1}),
        ("interrupt_python", {}),
        ("reset_python", {}),
    ]

    texts = []
    for name, arguments in calls:
        result = await content_client.call_tool(name, arguments)
        texts.append(assert_content_only_result(result))

    assert texts[0].startswith("kernel: idle")
    assert texts[1].startswith("all-tools-content-only")
    assert texts[2].startswith("execution exec-000001: completed")
    assert texts[3].startswith("all-tools-content-only")
    assert texts[4].startswith("M 1 | all-tools-content-only")
    assert texts[5].startswith("all-tools-content-only")
    assert texts[6] == "中断：kernel 当前空闲，无需 interrupt。"
    assert texts[7] == "重置：kernel 已重启。"


async def test_standard_server_still_returns_structured_content_and_data(valid_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(valid_workspace)
    async with Client(create_standard_mcp()) as standard_client:
        result = await standard_client.call_tool("python_status", {})

    assert result.structured_content is not None
    assert result.data == result.structured_content
    assert result.data["kernel_started"] is True
    assert result_text(result).startswith(f"kernel: idle\nworkspace: {valid_workspace}\npython: ")
