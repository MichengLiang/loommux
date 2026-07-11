from __future__ import annotations

import stat
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from loommux.adapter import IPythonMCPAdapter, parse_run_python_freeform_timeout
from loommux.mcp_ipython_content_server import create_mcp as create_content_mcp


@pytest.fixture
async def content_client() -> AsyncIterator[Client[Any]]:
    async with Client(create_content_mcp()) as mcp_client:
        yield mcp_client


@pytest.fixture
def valid_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    python_path = workspace / ".venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n")
    python_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return workspace


def result_text(result: Any) -> str:
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    return result.content[0].text


@pytest.mark.parametrize(
    ("source", "expected_timeout", "expected_source"),
    [
        ("", 10.0, "default"),
        ("print('no directive')", 10.0, "default"),
        ("# loommux: timeout_seconds=120", 120.0, "directive"),
        ("# loommux: timeout_seconds=0.5", 0.5, "directive"),
        ("# loommux: timeout_seconds=0", 10.0, "default"),
        ("# loommux: timeout_seconds=01", 10.0, "default"),
        ("# loommux: timeout_seconds=-1", 10.0, "default"),
        ("# loommux: timeout_seconds=.5", 10.0, "default"),
        ("# loommux: timeout_seconds=1e3", 10.0, "default"),
        ("# loommux: timeout_seconds = 120", 10.0, "default"),
        ("# loommux: timeout_seconds=1\n# loommux: timeout_seconds=2", 10.0, "default"),
        ("# regular comment\n# loommux: timeout_seconds=3\nprint(42)", 3.0, "directive"),
        ("print('windows')\r\n# loommux: timeout_seconds=4\r\n", 4.0, "directive"),
    ],
)
def test_parse_run_python_freeform_timeout_boundaries(source: str, expected_timeout: float, expected_source: str) -> None:
    assert parse_run_python_freeform_timeout(source) == (expected_timeout, expected_source)


def test_run_python_calls_private_submit_with_original_source_and_default_timeout() -> None:
    class CapturingAdapter(IPythonMCPAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[tuple[str, float]] = []

        def _submit_python_cell(self, code: str, timeout_seconds: float) -> dict[str, Any]:
            self.calls.append((code, timeout_seconds))
            return {"ok": True, "status": "captured"}

    adapter = CapturingAdapter()
    source = "# loommux: timeout_seconds = 0.5\nprint('still runs')"

    result = adapter.run_python(source)

    assert result["status"] == "captured"
    assert adapter.calls == [(source, 10.0)]
    assert not hasattr(adapter, "timeout_seconds")
    assert not hasattr(adapter, "run_python_timeout_seconds")


def test_run_python_uses_unique_valid_directive_and_falls_back_on_ambiguity() -> None:
    class CapturingAdapter(IPythonMCPAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[tuple[str, float]] = []

        def _submit_python_cell(self, code: str, timeout_seconds: float) -> dict[str, Any]:
            self.calls.append((code, timeout_seconds))
            return {"ok": True, "status": "captured"}

    adapter = CapturingAdapter()
    valid_source = "# loommux: timeout_seconds=0.5\nprint('valid')"
    ambiguous_source = "# loommux: timeout_seconds=0.5\n# loommux: timeout_seconds=1\nprint('ambiguous')"

    adapter.run_python(valid_source)
    adapter.run_python(ambiguous_source)

    assert adapter.calls == [(valid_source, 0.5), (ambiguous_source, 10.0)]


async def test_content_run_python_schema_is_freeform_only(content_client: Client[Any]) -> None:
    tools = await content_client.list_tools()
    run_python = next(tool for tool in tools if tool.name == "run_python")
    schema = run_python.inputSchema

    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"freeform"}
    assert schema["properties"]["freeform"] == {"type": "string"}
    assert schema["properties"]["freeform"]["type"] == "string"
    assert schema["required"] == ["freeform"]
    assert schema["additionalProperties"] is False
    assert "code" not in schema["properties"]
    assert "timeout_seconds" not in schema["properties"]


async def test_content_run_python_docstring_only_teaches_canonical_directive(content_client: Client[Any]) -> None:
    tools = await content_client.list_tools()
    run_python = next(tool for tool in tools if tool.name == "run_python")
    description = run_python.description or ""

    assert "# loommux: timeout_seconds=120" in description
    assert "输入\n----" in description
    assert "等待上限\n--------" in description
    assert "返回表面\n--------" in description
    assert "后续工具\n--------" in description
    assert "10 秒" in description
    assert "python-output:<execution_id>" in description
    assert "/stdout" in description
    assert "/stderr" in description
    assert "/result" in description
    assert "/traceback" in description
    assert "read_python_output" in description
    assert "search_python_output" in description
    assert "wait_python" in description
    assert "python_execution_status" in description
    assert "interrupt_python" in description
    assert "reset_python" in description
    assert "LOOMMUX_RUN_TIMEOUT_SECONDS" not in description
    assert "timeout_seconds = 120" not in description


async def test_freeform_valid_invalid_multiple_nonpersistent_and_no_runtime_injection(content_client: Client[Any], valid_workspace: Path) -> None:
    await content_client.call_tool("set_workspace", {"path": str(valid_workspace)})

    valid = await content_client.call_tool("run_python", {"freeform": "# loommux: timeout_seconds=0.1\nimport time\ntime.sleep(0.5)\n'first'"})
    assert "running" in result_text(valid)
    await content_client.call_tool("wait_python", {"execution_id": "exec-000001", "timeout_seconds": 5})

    nonpersistent = await content_client.call_tool("run_python", {"freeform": "import time\ntime.sleep(0.2)\n'second'"})
    assert "second" in result_text(nonpersistent)

    invalid = await content_client.call_tool("run_python", {"freeform": "# loommux: timeout_seconds = 0.1\nimport time\ntime.sleep(0.2)\n'invalid-fallback'"})
    assert "invalid-fallback" in result_text(invalid)

    multiple = await content_client.call_tool("run_python", {"freeform": "# loommux: timeout_seconds=0.1\n# loommux: timeout_seconds=0.1\nimport time\ntime.sleep(0.2)\n'multiple-fallback'"})
    assert "multiple-fallback" in result_text(multiple)

    runtime = await content_client.call_tool("run_python", {"freeform": "# loommux: timeout_seconds=1\nany(name.startswith('LOOMMUX_') for name in globals())"})
    assert "False" in result_text(runtime)
