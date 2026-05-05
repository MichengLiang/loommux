from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP
from fastmcp.tools import ToolResult

from loommux.adapter import IPythonMCPAdapter
from loommux.mcp_result_policy import make_tool_result


def _tool_result(tool_name: str, raw_status: dict[str, Any]) -> ToolResult:
    return make_tool_result(tool_name, raw_status, "content_only")


def create_mcp() -> FastMCP:
    adapter = IPythonMCPAdapter()

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        try:
            yield {"adapter": adapter}
        finally:
            adapter.close()

    mcp = FastMCP("loommux IPython MCP adapter (content only)", lifespan=lifespan)

    @mcp.tool(output_schema=None)
    def set_workspace(path: str) -> ToolResult:
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

    @mcp.tool(output_schema=None)
    def run_python(freeform: str) -> ToolResult:
        """向当前 IPython kernel 提交 Python cell，并等待至完成或达到等待上限。

        输入
        ----

        ``freeform`` 是原始 Python cell 源码文本。该文本原样提交给当前
        持久 IPython kernel。

        等待上限
        --------

        本次调用默认等待 10 秒。若本次调用需要等待更长时间，在 cell 中
        放入且只放入一行完整匹配的注释::

            # loommux: timeout_seconds=120

        没有该注释、该注释无效或存在多条有效注释时，本次调用仍等待
        10 秒。等待上限只控制本次工具调用等待多久；达到等待上限后
        execution 继续在 kernel 中运行，不会被 interrupt 或 reset。

        返回表面
        --------

        已结束且 combined output 不超过 300 行时直接展示可见输出。running
        或大输出时返回 ``status="running"``、``execution_id`` 和
        ``output_log``。``output_log`` 是 combined output log handle，
        格式为 ``python-output:<execution_id>``。分流日志由该 handle 加
        固定后缀 ``/stdout``、``/stderr``、``/result``、``/traceback`` 派生。

        后续工具
        --------

        读取日志使用 ``read_python_output``。搜索日志使用
        ``search_python_output``。等待运行中 execution 使用 ``wait_python``。
        查看 execution 结构化状态使用 ``python_execution_status``。中断或
        重启使用 ``interrupt_python`` 或 ``reset_python``。

        :returns: execution 结果。小输出直接返回 Python 可见输出；running 或
            大输出返回 ``execution_id``、``status``、``output_log``、
            ``output_omitted_reason`` 和已收集行数。
        """
        return _tool_result("run_python", adapter.run_python(freeform))

    @mcp.tool(output_schema=None)
    def python_status() -> ToolResult:
        """返回当前 `workspace` 与 `kernel` 的状态快照。

        Returns:
            状态字典。返回内容描述当前激活的 `workspace`、解释器、
            `kernel` 进程、执行计数以及 `kernel` 是否处于 busy 状态。
        """
        return _tool_result("python_status", adapter.python_status())

    @mcp.tool(output_schema=None)
    def python_execution_status(execution_id: str | None = None) -> ToolResult:
        """返回某个 execution 的结构化状态，不返回完整日志正文。

        返回 canonical output log handle `python-output:<execution_id>`。
        分流日志由该 handle 加 `/stdout`、`/stderr`、`/result`、
        `/traceback` 派生。

        Args:
            execution_id: 要读取的 execution 标识。未提供时，工具优先读取
                当前 execution；若当前 execution 不存在，则读取最近一次
                execution。

        Returns:
            状态字典。返回内容包括 execution 状态、时间信息、错误摘要和
            canonical output log handle。分流日志由 `output_log` 加固定后缀
            `/stdout`、`/stderr`、`/result`、`/traceback` 派生。
        """
        return _tool_result("python_execution_status", adapter.python_execution_status(execution_id))

    @mcp.tool(output_schema=None)
    def read_python_output(execution_id: str | None = None, output_log: str | None = None, stream: str = "combined", line_range: str | None = None, show_line_numbers: bool = False, max_chars: int | None = None) -> ToolResult:
        """读取 execution output log 的文本行。

        可用 execution_id 或 output_log 选择目标。`python-output:<execution_id>`
        表示 combined log；后缀 `/stdout`、`/stderr`、`/result`、
        `/traceback` 表示分流日志。stream 选择分流；line_range 使用
        `start:stop`，`:10` 读取前 10 行，`-10:` 读取后 10 行；max_chars
        只裁切单行。

        Args:
            execution_id: execution 标识。未提供 `output_log` 时，读取该
                execution 的 combined log。未提供该参数时，优先读取 current
                execution，其次读取 last execution。
            output_log: output log handle。`python-output:<execution_id>` 表示
                combined log；后缀 `/stdout`、`/stderr`、`/result`、`/traceback`
                表示对应分流日志。
            stream: 要读取的 stream，支持 `combined`、`stdout`、`stderr`、
                `result`、`traceback`。如果 `output_log` 已包含 stream 后缀，
                该后缀决定 stream；显式传入冲突 stream 会返回
                `invalid_output_log`。
            line_range: 行范围，使用 `start:stop`。正数端点按 1-indexed line
                number 解释；端点可省略；负数端点按从日志尾部相对定位解释；
                stop 为包含端点。`:10` 读取前 10 行，`-10:` 读取后 10 行，
                `20:40` 读取第 20 到第 40 行。
            show_line_numbers: 是否在返回文本中显示 1-indexed 行号。
            max_chars: 每一行的最大显示宽度。该参数只裁切单行，不裁切整段
                结果。

        Returns:
            日志读取结果，包括 `output_log`、`stream`、`total_lines`、
            `returned_lines`、省略行数和 `text`。
        """
        return _tool_result("read_python_output", adapter.read_python_output(execution_id=execution_id, output_log=output_log, stream=stream, line_range=line_range, show_line_numbers=show_line_numbers, max_chars=max_chars))

    @mcp.tool(output_schema=None)
    def search_python_output(query: str, execution_id: str | None = None, output_log: str | None = None, stream: str = "combined", query_mode: str = "auto", context_before: int = 0, context_after: int = 0, ignore_case: bool = False, max_chars: int | None = None) -> ToolResult:
        """搜索 execution output log。

        可用 execution_id 或 output_log 选择目标。`python-output:<execution_id>`
        表示 combined log；后缀 `/stdout`、`/stderr`、`/result`、
        `/traceback` 表示分流日志。stream 选择分流；query_mode 支持
        literal、regex、auto；context_before/context_after 返回上下文；
        ignore_case 控制大小写。

        Args:
            query: 要搜索的字面量或正则模式。
            execution_id: execution 标识。未提供 `output_log` 时，搜索该
                execution 的 combined log。未提供该参数时，优先搜索 current
                execution，其次搜索 last execution。
            output_log: output log handle。`python-output:<execution_id>` 表示
                combined log；后缀 `/stdout`、`/stderr`、`/result`、`/traceback`
                表示对应分流日志。
            stream: 要搜索的 stream，支持 `combined`、`stdout`、`stderr`、
                `result`、`traceback`。如果 `output_log` 已包含 stream 后缀，
                该后缀决定 stream；显式传入冲突 stream 会返回
                `invalid_output_log`。
            query_mode: `literal` 按字面量搜索；`regex` 按正则搜索；`auto`
                优先按正则解释，正则编译失败时回退为字面量搜索。
            context_before: 每条命中前返回的上下文行数。
            context_after: 每条命中后返回的上下文行数。
            ignore_case: 是否忽略大小写。
            max_chars: 每一行的最大显示宽度。该参数只裁切单行，不裁切整段
                结果。

        Returns:
            搜索结果，包括 `output_log`、`stream`、`query_interpretation`、
            `matched_lines`、`matches`、上下文设置和 `text`。
        """
        return _tool_result("search_python_output", adapter.search_python_output(query=query, execution_id=execution_id, output_log=output_log, stream=stream, query_mode=query_mode, context_before=context_before, context_after=context_after, ignore_case=ignore_case, max_chars=max_chars))

    @mcp.tool(output_schema=None)
    def wait_python(execution_id: str | None = None, timeout_seconds: float = 30) -> ToolResult:
        """等待某个 execution 完成，或在达到超时后返回。

        已结束且 combined output 不超过 300 行时直接展示可见输出；
        running 或大输出时返回 output_log combined handle
        `python-output:<execution_id>`。分流日志由该 handle 加
        `/stdout`、`/stderr`、`/result`、`/traceback` 派生；读取日志使用
        `read_python_output`，搜索日志使用 `search_python_output`，查看
        execution 结构化状态使用 `python_execution_status`。

        Args:
            execution_id: 要等待的 execution 标识。未提供时，工具优先等待
                当前 execution；若当前 execution 不存在，则等待最近一次
                execution。
            timeout_seconds: 返回前最多等待的秒数。

        Returns:
            等待后的 execution 结果。已结束且 combined output log 不超过
            300 行时直接展示 Python 可见输出；仍在运行或 combined output
            log 超过 300 行时只返回 `execution_id`、`status`、`output_log`
            和省略原因。分流日志由 `output_log` 加固定后缀 `/stdout`、
            `/stderr`、`/result`、`/traceback` 派生。
        """
        return _tool_result("wait_python", adapter.wait_python(execution_id, timeout_seconds))

    @mcp.tool(output_schema=None)
    def interrupt_python() -> ToolResult:
        """向当前运行中的 execution 发送中断信号。

        Returns:
            状态字典。返回内容说明当前 `kernel` 是处于 idle、不可用，
            还是已经向当前 execution 发送了中断信号。
        """
        return _tool_result("interrupt_python", adapter.interrupt_python())

    @mcp.tool(output_schema=None)
    def reset_python() -> ToolResult:
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
