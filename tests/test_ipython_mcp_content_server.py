from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from loommux.mcp_ipython_content_server import create_mcp as create_content_mcp
from loommux.mcp_ipython_server import create_mcp as create_standard_mcp
from loommux.mcp_result_policy import make_tool_result

WORKSPACE_CONFIG_ENV = "LOOMMUX_WORKSPACE_CONFIG"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    value = tmp_path / "workspace"
    value.mkdir()
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


async def test_default_workspace_ignores_legacy_files_and_exposes_launch_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    launch_cwd = project / "nested" / "launch"
    marker = tmp_path / "legacy-executed"
    launch_cwd.mkdir(parents=True)
    (project / "loommux_workspace.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n", encoding="utf-8")
    (launch_cwd / "loommux_workspace.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n", encoding="utf-8")
    monkeypatch.chdir(launch_cwd)
    monkeypatch.delenv(WORKSPACE_CONFIG_ENV, raising=False)

    async with Client(create_standard_mcp()) as client:
        status = await client.call_tool("python_status", {})
        cwd = await client.call_tool("run_python", {"freeform": "import os\nprint(os.getcwd())"})

    assert status.data["workspace"] == str(launch_cwd)
    assert status.data["workspace_resolution"] == "launch_cwd"
    assert cwd.content[0].text == f"In [1]:\n{launch_cwd}\n"
    assert not marker.exists()


async def test_explicit_resolver_controls_server_workspace_and_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launch_cwd = tmp_path / "launch"
    workspace = tmp_path / "workspace"
    resolver = tmp_path / "resolver.py"
    launch_cwd.mkdir()
    workspace.mkdir()
    resolver.write_text("def resolve_workspace(launch_cwd):\n    return launch_cwd.parent / 'workspace'\n", encoding="utf-8")
    monkeypatch.chdir(launch_cwd)
    monkeypatch.setenv(WORKSPACE_CONFIG_ENV, str(resolver))

    async with Client(create_standard_mcp()) as client:
        status = await client.call_tool("python_status", {})
        cwd = await client.call_tool("run_python", {"freeform": "import os\nprint(os.getcwd())"})

    assert status.data["workspace"] == str(workspace)
    assert status.data["workspace_resolution"] == "explicit_config"
    assert cwd.content[0].text == f"In [1]:\n{workspace}\n"


@pytest.mark.parametrize("factory", [create_standard_mcp, create_content_mcp])
async def test_configured_resolution_errors_prevent_each_entrypoint_from_starting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, factory: Any) -> None:
    launch_cwd = tmp_path / "launch"
    launch_cwd.mkdir()
    resolver_directory = tmp_path / "resolver-directory"
    resolver_directory.mkdir()
    (launch_cwd / "workspace-file").write_text("not a directory", encoding="utf-8")
    cases = [
        ("relative.py", "workspace_config_not_absolute"),
        (str(tmp_path / "missing.py"), "workspace_config_not_found"),
        (str(resolver_directory), "workspace_config_not_file"),
    ]
    for filename, source, status in [
        ("load-error.py", "raise RuntimeError('private resolver source')\n", "workspace_config_load_failed"),
        ("missing-resolver.py", "PRIVATE_RESOLVER_CONTENT = 'private resolver source'\n", "workspace_config_load_failed"),
        ("invalid-return.py", "def resolve_workspace(launch_cwd):\n    return 3\n", "workspace_config_invalid_return"),
        ("missing-workspace.py", "def resolve_workspace(launch_cwd):\n    return 'missing'\n", "workspace_not_found"),
        ("file-workspace.py", "def resolve_workspace(launch_cwd):\n    return 'workspace-file'\n", "workspace_not_directory"),
    ]:
        resolver = tmp_path / filename
        resolver.write_text(source, encoding="utf-8")
        cases.append((str(resolver), status))
    monkeypatch.chdir(launch_cwd)

    for configured_path, expected_status in cases:
        monkeypatch.setenv(WORKSPACE_CONFIG_ENV, configured_path)
        with pytest.raises(RuntimeError, match=expected_status) as error:
            async with Client(factory()):
                pytest.fail("MCP tools must not become usable after resolver failure")
        assert "private resolver source" not in str(error.value)


async def test_both_entrypoints_use_the_server_interpreter_and_preserve_workspace_resolution(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(workspace)
    expected_python = str(Path(sys.executable).absolute())
    async with Client(create_standard_mcp()) as dual, Client(create_content_mcp()) as content:
        dual_status = await dual.call_tool("python_status", {})
        content_status = await content.call_tool("python_status", {})
        dual_python = await dual.call_tool("run_python", {"freeform": "import sys\nprint(sys.executable)"})
        content_python = await content.call_tool("run_python", {"freeform": "import sys\nprint(sys.executable)"})
        reset = await dual.call_tool("reset_python", {})

    assert dual_status.data["python"] == expected_python
    assert dual_status.data["workspace_resolution"] == "launch_cwd"
    assert "workspace_resolution: launch_cwd" in content_status.content[0].text
    assert expected_python in dual_python.content[0].text
    assert expected_python in content_python.content[0].text
    assert reset.data["workspace_resolution"] == "launch_cwd"


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
    assert "# loommux: full_output" in run_python
    assert "300 行" in run_python
    assert "wait_python" in run_python
    assert "execution_id" not in run_python and "output_log" not in run_python
    assert "原始 Python cell 源码文本" in tools["run_python"].inputSchema["properties"]["freeform"]["description"]

    status = tools["python_execution_status"].description or ""
    assert "选择规则\n--------" in status
    assert "当前\nrunning 记录" in status
    assert "正整数执行编号" in tools["python_execution_status"].inputSchema["properties"]["execution"]["description"]

    python_status = tools["python_status"].description or ""
    assert "workspace_resolution" in python_status
    assert "launch_cwd" in python_status and "explicit_config" in python_status
    assert "resolver 的路径或内容" in python_status
    assert "private runtime root" in python_status

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
    assert "完整 combined 正文" in wait
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
    assert dual.content[0].text == content.content[0].text == "In [1]:"


async def test_mcp_content_projects_input_coordinate_display_result_and_traceback(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(workspace)
    async with Client(create_standard_mcp()) as dual_client, Client(create_content_mcp()) as content_client:
        dual_silent = await dual_client.call_tool("run_python", {"freeform": "value = 1"})
        content_silent = await content_client.call_tool("run_python", {"freeform": "value = 1"})
        dual_result = await dual_client.call_tool("run_python", {"freeform": "value"})
        content_result = await content_client.call_tool("run_python", {"freeform": "value"})
        dual_error = await dual_client.call_tool("run_python", {"freeform": "1 / 0"})
        content_error = await content_client.call_tool("run_python", {"freeform": "1 / 0"})

    assert dual_silent.content[0].text == content_silent.content[0].text == "In [1]:"
    assert dual_result.content[0].text == content_result.content[0].text == "In [2]:\nOut[2]: 1\n"
    assert dual_error.content[0].text.startswith("In [3]:\n")
    assert content_error.content[0].text.startswith("In [3]:\n")
    assert "ZeroDivisionError" in dual_error.content[0].text
    assert "ZeroDivisionError" in content_error.content[0].text


async def test_result_policies_share_marked_complete_long_output(content_client: Client[Any]) -> None:
    source = "# loommux: full_output\nprint('\\n'.join(f'line-{number}' for number in range(301)))"
    async with Client(create_standard_mcp()) as dual_client:
        dual = await dual_client.call_tool("run_python", {"freeform": source})
    content = await content_client.call_tool("run_python", {"freeform": source})

    expected = "In [1]:\n" + "\n".join(f"line-{number}" for number in range(301)) + "\n"
    assert dual.content[0].text == content.content[0].text == expected
    assert dual.structured_content is not None
    assert dual.structured_content["output_omitted"] is False
    assert content.structured_content is None


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
    assert wait.content[0].text == "In [1]:\nfactory\n"
    assert interrupt.content[0].text == "Python kernel is idle."
    assert "sequence is preserved" in reset.content[0].text


async def test_real_mcp_eight_tool_loop_preserves_public_sequence_across_reset(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(workspace)
    async with Client(create_standard_mcp()) as client:
        initial = await client.call_tool("python_status", {})
        small = await client.call_tool("run_python", {"freeform": "print('small-output')"})
        running = await client.call_tool(
            "run_python",
            {"freeform": "# loommux: timeout_seconds=0.1\nimport time\nprint('long-start', flush=True)\ntime.sleep(1.5)\nprint('long-finished', flush=True)"},
        )
        long_execution = running.data["execution"]
        observed = await client.call_tool("python_execution_status", {"execution": long_execution})
        partial = await client.call_tool("read_python_output", {"execution": long_execution, "stream": "stdout"})
        found = await client.call_tool("search_python_output", {"execution": long_execution, "stream": "stdout", "query": "long-start", "query_mode": "literal"})
        completed = await client.call_tool("wait_python", {"execution": long_execution, "timeout_seconds": 3})
        interruptible = await client.call_tool(
            "run_python",
            {"freeform": "# loommux: timeout_seconds=0.1\nimport time\nprint('interrupt-ready', flush=True)\ntime.sleep(5)"},
        )
        interrupted_execution = interruptible.data["execution"]
        interrupt = await client.call_tool("interrupt_python", {})
        interrupted = await client.call_tool("wait_python", {"execution": interrupted_execution, "timeout_seconds": 3})
        reset = await client.call_tool("reset_python", {})
        old_record = await client.call_tool("read_python_output", {"execution": small.data["execution"], "stream": "stdout"})
        after_reset = await client.call_tool("run_python", {"freeform": "print('after-reset')"})

    assert initial.data["workspace"] == str(workspace)
    assert initial.data["workspace_resolution"] == "launch_cwd"
    assert small.data["execution"] == 1
    assert long_execution == 2
    assert running.data["status"] == "running"
    assert observed.data["status"] == "running"
    assert partial.content[0].text == "long-start"
    assert "M 1 | long-start" in found.content[0].text
    assert completed.data["status"] == "completed"
    assert "long-finished" in completed.content[0].text
    assert interrupted_execution == 3
    assert interrupt.data["status"] == "interrupt_sent"
    assert interrupt.data["execution"] == interrupted_execution
    assert interrupted.data["status"] == "interrupted"
    assert reset.data["status"] == "restarted"
    assert old_record.content[0].text == "small-output"
    assert after_reset.data["execution"] == 4
    assert after_reset.content[0].text == "In [4]:\nafter-reset\n"


def test_mcp_result_policy_only_changes_structured_channel() -> None:
    raw = {"ok": True, "execution": 4, "status": "completed", "result_text": "1", "output_text": "Out[4]: 1\n", "output_omitted": False}
    assert make_tool_result("run_python", raw, "dual_channel").structured_content == raw
    assert make_tool_result("run_python", raw, "content_only").structured_content is None
