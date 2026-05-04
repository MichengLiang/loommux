from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from fastmcp import FastMCP
from fastmcp.tools import ToolResult

from loommux.adapter import IPythonMCPAdapter
from loommux.presentation import format_tool_result_text


def _tool_result(tool_name: str, raw_status: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], ToolResult(content=format_tool_result_text(tool_name, raw_status), structured_content=raw_status))


def create_mcp() -> FastMCP:
    adapter = IPythonMCPAdapter()

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        try:
            yield {"adapter": adapter}
        finally:
            adapter.close()

    mcp = FastMCP("loommux IPython MCP adapter", lifespan=lifespan)

    @mcp.tool
    def set_workspace(path: str) -> dict[str, Any]:
        """设置当前 `workspace`，并为该 `workspace` 启动 `kernel`。

        Args:
            path: `workspace` 目录路径。该路径可以是绝对路径、相对于
                server 进程工作目录的相对路径，或使用 `~`。

        Returns:
            状态字典。返回内容包括解析后的 `workspace`、选定的 Python
            解释器路径，以及 `kernel` 是否启动成功。失败返回包含 `status`
            和 `message`。
        """
        return _tool_result("set_workspace", adapter.set_workspace(path))

    @mcp.tool
    def run_python(code: str, timeout_seconds: float = 30) -> dict[str, Any]:
        """向当前 `kernel` 提交 Python 代码，并在超时前等待执行结果。

        Args:
            code: 要在当前 `kernel` session 中执行的 Python 源代码。
            timeout_seconds: 返回前最多等待的秒数。若达到超时仍未完成，
                工具返回 `running` 状态，execution 继续在后台运行。

        Returns:
            状态字典。返回内容包括 `execution_id`、已捕获的输出、错误数据
            以及当前 `kernel` 状态。
        """
        return _tool_result("run_python", adapter.run_python(code, timeout_seconds))

    @mcp.tool
    def python_status() -> dict[str, Any]:
        """返回当前 `workspace` 与 `kernel` 的状态快照。

        Returns:
            状态字典。返回内容描述当前激活的 `workspace`、解释器、
            `kernel` 进程、执行计数以及 `kernel` 是否处于 busy 状态。
        """
        return _tool_result("python_status", adapter.python_status())

    @mcp.tool
    def python_execution_status(execution_id: str | None = None) -> dict[str, Any]:
        """返回某个 execution 的结构化状态，不返回完整日志正文。

        Args:
            execution_id: 要读取的 execution 标识。未提供时，工具优先读取
                当前 execution；若当前 execution 不存在，则读取最近一次
                execution。

        Returns:
            状态字典。返回内容包括 execution 状态、时间信息、错误摘要和
            output log handles。
        """
        return _tool_result("python_execution_status", adapter.python_execution_status(execution_id))

    @mcp.tool
    def read_python_output(execution_id: str | None = None, output_log: str | None = None, line_range: str | None = None, show_line_numbers: bool = False, max_chars: int | None = None) -> dict[str, Any]:
        """按行读取某个 execution output log 的文本内容。

        Args:
            execution_id: 要读取的 execution 标识。未提供 `output_log` 时，
                该参数选择 execution 的 combined output log。
            output_log: output log handle，例如 `python-output:exec-000001`
                或 `python-output:exec-000001/stderr`。
            line_range: 连续行范围。使用 `start:stop`；正数端点按
                1-indexed line number 解释；端点可省略；负索引按从
                日志尾部相对定位解释。
            show_line_numbers: 是否在返回文本中显示 1-indexed 行号。
            max_chars: 当前读取结果中每一行的最大显示宽度。

        Returns:
            日志读取结果。返回内容包括 log handle、stream、总行数、
            返回行数、省略行数和文本。
        """
        return _tool_result("read_python_output", adapter.read_python_output(execution_id, output_log, line_range, show_line_numbers, max_chars))

    @mcp.tool
    def search_python_output(query: str, execution_id: str | None = None, output_log: str | None = None, query_mode: str = "auto", context_before: int = 0, context_after: int = 0, ignore_case: bool = False, max_chars: int | None = None) -> dict[str, Any]:
        """搜索某个 execution output log 的文本内容。

        Args:
            query: 要搜索的文本或正则模式。
            execution_id: 要搜索的 execution 标识。未提供 `output_log` 时，
                该参数选择 execution 的 combined output log。
            output_log: output log handle。
            query_mode: `auto`、`literal` 或 `regex`。
            context_before: 每条命中前返回的上下文行数。
            context_after: 每条命中后返回的上下文行数。
            ignore_case: 是否忽略大小写。
            max_chars: 当前搜索结果中每一行的最大显示宽度。

        Returns:
            搜索结果。返回内容包括命中行数、匹配次数、上下文和文本。
        """
        return _tool_result("search_python_output", adapter.search_python_output(query, execution_id, output_log, query_mode, context_before, context_after, ignore_case, max_chars))

    @mcp.tool
    def wait_python(execution_id: str | None = None, timeout_seconds: float = 30) -> dict[str, Any]:
        """等待某个 execution 完成，或在达到超时后返回。

        Args:
            execution_id: 要等待的 execution 标识。未提供时，工具优先等待
                当前 execution；若当前 execution 不存在，则等待最近一次
                execution。
            timeout_seconds: 返回前最多等待的秒数。

        Returns:
            状态字典。返回内容包括等待结束时的 execution 快照，以及当前
            `kernel` 状态。
        """
        return _tool_result("wait_python", adapter.wait_python(execution_id, timeout_seconds))

    @mcp.tool
    def interrupt_python() -> dict[str, Any]:
        """向当前运行中的 execution 发送中断信号。

        Returns:
            状态字典。返回内容说明当前 `kernel` 是处于 idle、不可用，
            还是已经向当前 execution 发送了中断信号。
        """
        return _tool_result("interrupt_python", adapter.interrupt_python())

    @mcp.tool
    def reset_python() -> dict[str, Any]:
        """为当前 `workspace` 重启 `kernel`。

        Returns:
            状态字典。返回内容描述当前 `workspace` 下重启后的 `kernel`
            状态。若存在运行中的 execution，该 execution 会在新 `kernel`
            启动前被终止。
        """
        return _tool_result("reset_python", adapter.reset_python())

    return mcp


mcp = create_mcp()


if __name__ == "__main__":
    mcp.run()
