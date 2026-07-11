from __future__ import annotations

import stat
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from loommux.mcp_ipython_content_server import create_mcp as create_content_mcp
from loommux.mcp_ipython_server import create_mcp as create_standard_mcp
from loommux.mcp_result_policy import make_tool_result


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    value = tmp_path / "workspace"
    python = value / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f'#!/bin/sh\nexec {sys.executable} "$@"\n')
    python.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return value


@pytest.fixture
async def content_client(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Client[Any]]:
    monkeypatch.chdir(workspace)
    async with Client(create_content_mcp()) as client:
        yield client


async def test_entrypoints_have_identical_eight_tool_schemas_and_descriptions(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(workspace)
    async with Client(create_standard_mcp()) as dual, Client(create_content_mcp()) as content:
        dual_tools = {tool.name: tool for tool in await dual.list_tools()}
        content_tools = {tool.name: tool for tool in await content.list_tools()}

    expected = {"run_python", "python_status", "python_execution_status", "read_python_output", "search_python_output", "wait_python", "interrupt_python", "reset_python"}
    assert set(dual_tools) == set(content_tools) == expected
    for name in expected:
        assert dual_tools[name].inputSchema == content_tools[name].inputSchema
        assert dual_tools[name].description == content_tools[name].description
    schema = dual_tools["read_python_output"].inputSchema["properties"]
    assert set(schema) == {"execution", "stream", "line_range", "show_line_numbers", "max_chars"}
    assert schema["execution"]["anyOf"][0]["type"] == "integer"


async def test_tool_descriptions_expose_the_complete_chinese_operation_contract(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(workspace)
    async with Client(create_standard_mcp()) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    run_python = tools["run_python"].description or ""
    assert "向持久 IPython kernel 提交一个原始 Python cell。" in run_python
    assert "输入\n----" in run_python
    assert "等待上限\n--------" in run_python
    assert "执行编号与后续操作\n--------------------" in run_python
    assert "# loommux: timeout_seconds=120" in run_python
    assert "execution_id" not in run_python and "output_log" not in run_python
    assert "原始 Python cell 源码文本" in tools["run_python"].inputSchema["properties"]["freeform"]["description"]

    status = tools["python_execution_status"].description or ""
    assert "选择规则\n--------" in status
    assert "当前\nrunning 记录" in status
    assert "正整数执行编号" in tools["python_execution_status"].inputSchema["properties"]["execution"]["description"]

    read = tools["read_python_output"].description or ""
    assert "行坐标\n------" in read
    assert "``:10``" in read and "``-10:``" in read and "``3:3``" in read
    read_parameters = tools["read_python_output"].inputSchema["properties"]
    assert "省略时使用当前记录" in read_parameters["execution"]["description"]
    assert "从 1 开始的" in read_parameters["show_line_numbers"]["description"]
    assert "必须为正数" in read_parameters["max_chars"]["description"]

    search = tools["search_python_output"].description or ""
    assert "选择与匹配\n------------" in search
    assert "query_mode=\"auto\"" in search
    search_parameters = tools["search_python_output"].inputSchema["properties"]
    assert "字面文本或正则表达式" in search_parameters["query"]["description"]
    assert "必须大于或等于 0" in search_parameters["context_before"]["description"]
    assert "每个命中之后" in search_parameters["context_after"]["description"]
    assert "忽略大小写" in search_parameters["ignore_case"]["description"]

    wait = tools["wait_python"].description or ""
    assert "选择与等待\n----------" in wait
    assert "不中断 Python cell" in wait
    assert "默认 30 秒" in tools["wait_python"].inputSchema["properties"]["timeout_seconds"]["description"]

    interrupt = tools["interrupt_python"].description or ""
    assert "中断语义\n--------" in interrupt
    assert "IOPub ``idle``" in interrupt
    assert "信号已发送不等同于记录已终态" in interrupt

    reset = tools["reset_python"].description or ""
    assert "重置边界\n--------" in reset
    assert "连续的下一个编号" in reset


async def test_result_policies_share_content_but_only_dual_exposes_structured_status(content_client: Client[Any]) -> None:
    async with Client(create_standard_mcp()) as dual_client:
        dual = await dual_client.call_tool("run_python", {"freeform": "value = 1"})
    content = await content_client.call_tool("run_python", {"freeform": "value = 1"})

    assert dual.structured_content is not None
    assert dual.structured_content["execution"] == 1
    assert content.structured_content is None
    assert dual.content[0].text == content.content[0].text == "Execution 1 completed without a display result."


async def test_shared_factory_binds_every_tool_to_the_integer_contract(content_client: Client[Any]) -> None:
    await content_client.call_tool("python_status", {})
    submitted = await content_client.call_tool("run_python", {"freeform": "print('factory')"})
    execution = submitted.data
    # content-only intentionally has no data; the current-or-recent default still
    # exercises the same single selection path without any hidden alternate id.
    status = await content_client.call_tool("python_execution_status", {})
    read = await content_client.call_tool("read_python_output", {"stream": "stdout"})
    search = await content_client.call_tool("search_python_output", {"query": "factory", "stream": "stdout", "query_mode": "literal"})
    wait = await content_client.call_tool("wait_python", {"timeout_seconds": 1})
    interrupt = await content_client.call_tool("interrupt_python", {})
    reset = await content_client.call_tool("reset_python", {})

    assert execution is None
    assert "execution 1: completed" in status.content[0].text
    assert read.content[0].text == "factory"
    assert "M 1 | factory" in search.content[0].text
    assert "Execution 1 completed" in wait.content[0].text
    assert interrupt.content[0].text == "Python kernel is idle."
    assert "sequence is preserved" in reset.content[0].text


def test_mcp_result_policy_only_changes_structured_channel() -> None:
    raw = {"ok": True, "execution": 4, "status": "completed", "result_text": "1", "output_text": "Out[4]: 1\n", "output_omitted": False}
    assert make_tool_result("run_python", raw, "dual_channel").structured_content == raw
    assert make_tool_result("run_python", raw, "content_only").structured_content is None
