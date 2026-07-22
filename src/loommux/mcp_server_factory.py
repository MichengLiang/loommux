from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP
from fastmcp.tools import ToolResult

from loommux.adapter import IPythonMCPAdapter
from loommux.host_workspace_config import WorkspaceConfigError
from loommux.mcp_result_policy import ResultChannelPolicy, make_tool_result
from loommux.monitoring import MonitorPublisher, create_monitor_publisher, run_monitored_tool_call
from loommux.workspace_resolver import resolve_workspace_launch


def create_mcp(policy: ResultChannelPolicy, monitor_publisher: MonitorPublisher | None = None) -> FastMCP:
    publisher = monitor_publisher or create_monitor_publisher()
    adapter = IPythonMCPAdapter(monitor_publisher=publisher)

    def call(tool_name: str, arguments: dict[str, Any], operation: Callable[[], dict[str, Any]]) -> ToolResult:
        def monitored(call_id: str) -> dict[str, Any]:
            token = adapter.set_active_call_id(call_id)
            try:
                return operation()
            finally:
                adapter.reset_active_call_id(token)

        return make_tool_result(tool_name, run_monitored_tool_call(tool_name, arguments, publisher, monitored), policy)

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        try:
            resolution = resolve_workspace_launch()
        except WorkspaceConfigError as exc:
            adapter.close()
            raise RuntimeError(f"loommux workspace initialization failed: {exc.status}") from exc
        startup = adapter.start_workspace(resolution.workspace, resolution.workspace_resolution)
        if not startup["ok"]:
            adapter.close()
            raise RuntimeError(f"loommux failed to start the configured workspace: {startup['message']}")
        try:
            yield {"adapter": adapter}
        finally:
            adapter.close()

    mcp = FastMCP("loommux IPython MCP adapter", lifespan=lifespan)

    @mcp.tool(output_schema=None)
    def run_python(freeform: str) -> ToolResult:
        """向持久 IPython kernel 提交一个原始 Python cell。

        请你使用 IPython 的思想来优雅使用本系列工具。

        输入
        ----

        接受一段 loommux Python cell 源码。普通 Python 文本使用默认策略；
        若作者需要声明本次 cell 的观察策略，第一行使用 IPython cell magic
        ``%%loommux``。变量、导入和其他 namespace 状态会与同一服务器会话中
        的后续 cell 共享。

        等待上限
        --------

        本次调用默认最多等待 10 秒。若 cell 的第一行是下列 magic，其
        ``--wait`` 正有限十进制值只覆盖本次调用的等待上限::

            %%loommux --wait 120
            build_report()

        bare ``%%loommux`` 也使用 10 秒。重复选项、未知选项、缺少值或非正
        值会返回 ``invalid_loommux_magic``，不会分配 execution 或提交 kernel。
        等待到期不会中断仍在运行的 cell；magic 不改变 Python runtime 或后续
        调用的等待上限。

        完整输出
        --------

        若第一行 magic 包含 ``--full-output``，该 execution 在终态时直接
        交付完整 combined 正文，不受默认 300 行交付阈值限制::

            %%loommux --full-output
            print("\\n".join(generate_manifest()))

        ``--wait`` 与 ``--full-output`` 可以组合为
        ``%%loommux --wait 120 --full-output``。这些选项只作用于本次
        execution，且 authored magic line 保留在原始 source 中。在明确需要
        完整阅读某些信息，例如阅读某些文件、资料时，使用该选项避免无意义的
        反复阅读开销。

        图像展示
        --------

        IPython ``display()`` 产生的 PNG、JPEG、WEBP 或单帧 GIF 图像会按输出
        顺序直接交付给agent。普通 ``display(image)`` 使用高视觉细节；本次展示
        需要整体确认或密集文字时，分别书写 ``display(image,
        metadata={"detail": "low"})`` 或 ``display(image, metadata={"detail":
        "original"})``。``detail`` 只作用于这一处 ``display()`` 调用。

        执行编号与后续操作
        --------------------

        已接受的提交会分配一个正整数 ``execution``，它在服务器进程存续
        期间严格递增。若执行仍在运行，或未标记 execution 的 combined 输出
        超过 300 行，响应不携带完整输出正文；使用 ``wait_python`` 等待，使用
        ``python_execution_status`` 查看状态，使用 ``read_python_output``
        或 ``search_python_output`` 读取或搜索保留的输出。

        Args:
            freeform: 要提交的原始 Python cell 源码文本；可用第一行
                ``%%loommux`` 声明本次初始等待与完整输出策略。

        Returns:
            已接受 execution 的当前状态；完成的小输出直接进入模型内容，
            running 或行数受限状态给出 ``execution`` 与省略原因。
        """
        return call("run_python", {"freeform": freeform}, lambda: adapter.run_python(freeform))

    @mcp.tool(output_schema=None)
    def python_status() -> ToolResult:
        """返回 workspace、kernel 与最近执行记录的观察状态。

        状态范围
        --------

        返回 server 启动时解析的 workspace、其 ``workspace_resolution`` 来源
        类别、Python 解释器、kernel PID、kernel 是否已启动，以及 kernel 是否
        正忙。``workspace_resolution`` 只能是 ``launch_cwd`` 或
        ``explicit_config``；它不公开 resolver 的路径或内容，也不公开 kernel
        session 的 private runtime root。忙碌时 ``current_execution`` 是正在
        运行的正整数执行编号；空闲时 ``recent_execution`` 是最近一次被接受的
        执行编号。kernel-local execution count 只用于诊断，不能用来选择
        loommux execution。

        .. code-block:: text

            可见标签 In [N]
                    =
            loommux execution N
                    !=
            IPython kernel-local execution_count

        IPython 通过原生 ZMQ 协议连接，拥有完整能力。

        Returns:
            当前 server 与 kernel 的状态快照。
        """
        return call("python_status", {}, adapter.python_status)

    @mcp.tool(output_schema=None)
    def python_execution_status(execution: int | None = None) -> ToolResult:
        """返回一个 execution 的状态与元数据，不返回完整输出正文。

        选择规则
        --------

        提供 ``execution`` 时，只选择该正整数记录。省略时，先选择当前
        running 记录；不存在 running 记录时，选择最近一次被接受的记录；
        两者都不存在时返回 ``execution_not_found``。

        Args:
            execution: 要查看的正整数执行编号。省略时使用当前记录，随后
                使用最近记录。

        Returns:
            选中记录的 ``execution``、status、时间戳、提交时 kernel 元数据、
            输出总行数、输出省略原因与错误摘要。
        """
        return call("python_execution_status", {"execution": execution}, lambda: adapter.python_execution_status(execution))

    @mcp.tool(output_schema=None)
    def read_python_output(execution: int | None = None, stream: str = "combined", line_range: str | None = None, max_chars: int | None = None) -> ToolResult:
        """读取一个 execution 的指定输出流。

        选择与流
        ----------

        ``execution`` 的选择规则与 ``python_execution_status`` 相同。``stream``
        只能为 ``combined``、``stdout``、``stderr``、``result`` 或
        ``traceback``。

        行坐标
        ------

        ``line_range`` 使用 ``start:stop``。正数端点是从 1 开始的流行号，
        ``stop`` 包含在范围内；端点可省略，负数端点从所选流尾部计数。``:10``
        读取前十行，``-10:`` 读取后十行，``3:3`` 只读取第三行。

        完整读取
        --------

        调用者已确定需要完整消费所选流时，省略 ``line_range``。工具会在一次响应
        中返回全部行，无需把阅读拆成连续小范围。

        Args:
            execution: 要读取的正整数执行编号。省略时使用当前记录，随后
                使用最近记录。
            stream: 输出流；默认 ``combined``，其余值为 ``stdout``、``stderr``、
                ``result`` 与 ``traceback``。
            line_range: ``start:stop`` 行范围；已确定需要完整消费所选流时省略，
                工具会一次返回全部行，无需拆分为多个小范围。
            max_chars: 每个返回行允许显示的最大字符数；必须为正数。超出
                部分只在响应中裁切，不改变已保存文本或行坐标。

        Returns:
            所选流的文本、总行数、返回行数及范围外省略行数。
        """
        arguments = {"execution": execution, "stream": stream, "line_range": line_range, "max_chars": max_chars}
        return call("read_python_output", arguments, lambda: adapter.read_python_output(execution, stream, line_range, max_chars))

    @mcp.tool(output_schema=None)
    def search_python_output(query: str, execution: int | None = None, stream: str = "combined", query_mode: str = "auto", context_before: int = 0, context_after: int = 0, ignore_case: bool = False, max_chars: int | None = None) -> ToolResult:
        """在一个 execution 的指定输出流中搜索文本或正则表达式。

        选择与匹配
        ------------

        ``execution`` 的选择规则与 ``python_execution_status`` 相同；可选的
        ``stream`` 值与 ``read_python_output`` 相同。``query_mode="literal"``
        按字面文本匹配，``query_mode="regex"`` 要求 ``query`` 是有效正则，
        ``query_mode="auto"`` 先按正则解释，仅在编译失败时回退到字面匹配。

        命中上下文
        ----------

        每条命中保留原始所选流行号，并以 ``M`` 标记；前后上下文行以
        ``C`` 标记。

        Args:
            query: 要匹配的字面文本或正则表达式。
            execution: 要搜索的正整数执行编号。省略时使用当前记录，随后
                使用最近记录。
            stream: 输出流；默认 ``combined``，其余值为 ``stdout``、``stderr``、
                ``result`` 与 ``traceback``。
            query_mode: 匹配解释方式：``literal``、``regex`` 或 ``auto``；
                默认 ``auto``。
            context_before: 每个命中之前附加的相邻行数；必须大于或等于 0。
            context_after: 每个命中之后附加的相邻行数；必须大于或等于 0。
            ignore_case: 为 true 时忽略大小写。
            max_chars: 每个返回行允许显示的最大字符数；必须为正数，且只
                裁切响应文本。

        Returns:
            带 ``M`` / ``C`` 行标记的命中与上下文、匹配统计和所选流行数；
            无命中时返回零匹配结果。
        """
        arguments = {"query": query, "execution": execution, "stream": stream, "query_mode": query_mode, "context_before": context_before, "context_after": context_after, "ignore_case": ignore_case, "max_chars": max_chars}
        return call("search_python_output", arguments, lambda: adapter.search_python_output(query, execution, stream, query_mode, context_before, context_after, ignore_case, max_chars))

    @mcp.tool(output_schema=None)
    def wait_python(execution: int | None = None, timeout_seconds: float = 30) -> ToolResult:
        """等待一个 execution 结束，或在指定时限到达时返回其当前状态。

        选择与等待
        ----------

        ``execution`` 的选择规则与 ``python_execution_status`` 相同。等待
        到期只结束本次工具调用，不中断 Python cell。后续可再次调用本工具，
        或用 ``read_python_output`` 查看已到达的输出。

        完整输出交付
        ------------

        当所选 execution 的第一行 ``%%loommux`` 含 ``--full-output`` 且已
        达到终态时，本工具直接返回完整 combined 正文，不应用默认 300 行省略。
        仍在运行的此类 execution 继续返回 running，而非不完整正文。

        Args:
            execution: 要等待的正整数执行编号。省略时使用当前记录，随后
                使用最近记录。
            timeout_seconds: 本次调用最多等待的正数秒数；默认 30 秒。

        Returns:
            选中 execution 的当前状态和可返回的输出表面。未找到记录或
            非正等待时长返回对应错误。
        """
        return call("wait_python", {"execution": execution, "timeout_seconds": timeout_seconds}, lambda: adapter.wait_python(execution, timeout_seconds))

    @mcp.tool(output_schema=None)
    def interrupt_python() -> ToolResult:
        """向当前正在运行的 execution 发送 kernel 中断信号。

        中断语义
        --------

        该工具只作用于当前 running 记录。信号已发送不等同于记录已终态：
        kernel 到达 IOPub ``idle`` 后，记录才会报告 ``interrupted``、
        ``error`` 或其他最终状态。kernel 空闲时返回 idle。

        Returns:
            已发送信号时返回目标 ``execution``；kernel 空闲时返回 idle。
        """
        return call("interrupt_python", {}, adapter.interrupt_python)

    @mcp.tool(output_schema=None)
    def reset_python() -> ToolResult:
        """重启 IPython kernel，并保留 loommux 服务器会话中的 execution 历史。

        重置边界
        --------

        若存在 running execution，它会先标记为 ``killed``；随后旧 kernel
        停止并创建替代 kernel。已存在的记录及其输出流仍可按原整数
        ``execution`` 读取，下一次接受的 cell 使用连续的下一个编号。

        Returns:
            新 kernel 的状态与 PID；重启失败时返回 workspace 或 kernel
            启动错误。
        """
        return call("reset_python", {}, adapter.reset_python)

    return mcp
