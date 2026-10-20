"""Build MCP tools that consume the protocol-neutral IPython session."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from fastmcp import Context, FastMCP
from fastmcp.server.http import StarletteWithLifespan
from fastmcp.tools import ToolResult

from loommux.host_workspace_config import WorkspaceConfigError
from loommux.mcp.control import install_control_routes
from loommux.mcp.lease_ping import install_lease_aware_ping_handler
from loommux.mcp.result import ResultMode, make_tool_result
from loommux.mcp.stream_presence import StandaloneStreamPresence
from loommux.resource import (
    ConnectionRegistry,
    KernelResourceManager,
    LeasePolicy,
    LeasePolicyManager,
    ResourceManagerError,
    ResourceRoutingError,
    resolve_address,
    resolve_client,
    resolve_policy_generation,
)
from loommux.resource.settings import ResourceServerSettings
from loommux.session import IPythonSession
from loommux.workspace_resolver import resolve_workspace_launch

# Claude Code persists text results above 50,000 characters to a file unless the
# tool declares its own ceiling. Tools that deliver an execution's full body opt in
# to a higher host ceiling; loommux's own delivery policy is unchanged.
_FULL_BODY_TOOL_META = {"anthropic/maxResultSizeChars": 200_000}


class _LoommuxMCP(FastMCP):
    """A FastMCP server whose HTTP application reports stream presence.

    ``run_http_async`` builds its application through ``http_app`` as well,
    so tests and the installed command observe streams the same way.
    """

    def __init__(
        self,
        name: str,
        *,
        lifespan: Callable[[FastMCP], AbstractAsyncContextManager[dict[str, Any]]],
        connections: ConnectionRegistry,
    ) -> None:
        super().__init__(name, lifespan=lifespan)
        self._connections = connections

    # The arguments pass through untouched: run_http_async calls this with
    # keywords, and a FastMCP minor release that adds an http_app option must
    # keep working, so a pass-through is the one place Any is the honest type.
    def http_app(self, *args: Any, **kwargs: Any) -> StarletteWithLifespan:  # noqa: ANN401
        app = super().http_app(*args, **kwargs)
        app.add_middleware(StandaloneStreamPresence, connections=self._connections)
        return app


def create_mcp(result_mode: ResultMode, *, control_url: str | None = None) -> FastMCP:
    """Build one MCP server whose tools consume selected kernel resources.

    ``control_url`` is the origin of this server's HTTP control plane as its
    kernels reach it; every kernel learns it at start.
    """

    manager: KernelResourceManager | None = None
    connections = ConnectionRegistry()

    async def call(
        tool_name: str,
        ctx: Context,
        operation: Callable[[IPythonSession], dict[str, Any]],
        *,
        replaces_kernel: bool = False,
    ) -> ToolResult:
        selected_manager = manager
        if selected_manager is None:
            status = {
                "ok": False,
                "status": "resource_manager_not_started",
                "message": "kernel resource manager is not started",
            }
            return make_tool_result(tool_name, status, result_mode)
        try:
            address = resolve_address(ctx)
            client = resolve_client(ctx)
            request_context = ctx.request_context
            if request_context is not None and request_context.request is None:
                # No HTTP request means stdio or an in-process client: the
                # session lives exactly as long as this server process.
                connections.bind_to_process(client.client_id)
            generation = resolve_policy_generation()
            policy: LeasePolicy | None
            if generation is None:
                policy = await selected_manager.policy_manager.current()
            else:
                policy = await selected_manager.policy_manager.generation(generation)
            if policy is None:
                raise ResourceRoutingError(
                    f"server does not know lease policy generation {generation}"
                )
            async with selected_manager.operation(
                address,
                client,
                policy,
                replaces_kernel=replaces_kernel,
            ) as resource:
                status = await asyncio.to_thread(operation, resource.session)
            verdict = await selected_manager.client_verdict(address.key, client.client_id)
            if verdict is not None and verdict.standing.alert:
                status = {
                    **status,
                    "lease_alert": {
                        "standing": verdict.standing.value,
                        "fallback_window_seconds": verdict.window_seconds,
                    },
                }
        except (ResourceManagerError, ResourceRoutingError) as exc:
            status = {
                "ok": False,
                "status": "resource_unavailable",
                "message": str(exc),
            }
        return make_tool_result(tool_name, status, result_mode)

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        nonlocal manager
        try:
            resolution = resolve_workspace_launch()
        except WorkspaceConfigError as exc:
            raise RuntimeError(f"loommux workspace initialization failed: {exc.status}") from exc
        settings = ResourceServerSettings.from_environ()
        policy_manager = LeasePolicyManager(
            initial_mode=settings.lease_mode,
            private_activity_timeout_seconds=settings.private_activity_timeout_seconds,
            named_activity_timeout_seconds=settings.named_activity_timeout_seconds,
            heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
            heartbeat_timeout_seconds=settings.heartbeat_timeout_seconds,
            connection_grace_seconds=settings.connection_grace_seconds,
        )
        manager = KernelResourceManager(
            resolution.workspace,
            resolution.workspace_resolution,
            policy_manager=policy_manager,
            connections=connections,
            sweep_interval_seconds=settings.sweep_interval_seconds,
            orphan_grace_seconds=settings.orphan_grace_seconds,
            control_url=control_url,
        )
        await manager.start()
        try:
            yield {"resource_manager": manager}
        finally:
            await manager.stop()
            manager = None

    mcp = _LoommuxMCP(
        "loommux persistent IPython session",
        lifespan=lifespan,
        connections=connections,
    )
    install_lease_aware_ping_handler(mcp, lambda: manager)
    install_control_routes(mcp, lambda: manager)

    @mcp.tool(output_schema=None, meta=_FULL_BODY_TOOL_META)
    async def run_cell(freeform: str, ctx: Context) -> ToolResult:
        """向持久 IPython kernel 提交一个原始 IPython cell。

        请你使用 IPython 的思想来优雅使用本系列工具。

        输入
        ----

        接受一段 IPython cell 源码，直接写代码即可。变量、导入和其他 namespace
        状态与当前持久 IPython 会话中的后续 cell 共享。

        cell 头
        -------

        需要控制这一个 cell 时，在第一行写 ``%%loommux`` 头，它是普通的
        IPython cell magic 语法::

            %%loommux --wait 120 --full-output
            build_report()

        ``--wait SECONDS`` 把本次调用的等待上限从默认 10 秒改为正有限秒数；
        等待到期不会中断仍在运行的 cell。``--full-output`` 让这次 execution 在
        终态时直接交付完整 combined 正文，不受默认 5,000-token 交付阈值限制；
        在明确需要完整阅读某些文件、资料时使用，避免反复阅读的开销。选项只作用
        于这一个 cell。头的下一行可以接另一个 cell magic，例如 ``%%bash``。
        无效的头返回 ``invalid_cell_header``，不分配 execution，也不提交 kernel。

        头里用 ``--kernel NAME`` 写另一个 kernel 的名字，把这个 cell 交给它::

            %%loommux --kernel build --wait 120
            %%bash
            uv run pytest

        名叫 ``build`` 的 kernel 不存在时首次使用就开出，它跟着当前 kernel
        活、跟着它回收。它的输出实时转回这个 cell，它的错误也让这个 cell
        以一行 ``ForwardedCellError`` 结束；中断这个 cell 会一并中断它。每个
        kernel 里都有 ``loommux`` 对象：``loommux.kernels()`` 列出所有
        kernel，``loommux["build"].run(code)`` 在后台提交并返回 job。

        图像展示
        --------

        IPython ``display()`` 产生的 PNG、JPEG、WEBP 或单帧 GIF 图像会按输出
        顺序直接交付给agent。

        执行编号与后续操作
        --------------------

        每个已接受的提交都会获得一个连续递增的正整数 ``execution``。后续工具
        使用它定位当前持久 IPython 会话中的这次执行。若执行仍在运行，或未标记
        ``--full-output`` 的 combined 输出超过 5,000 token，响应不携带完整输出
        正文；行数、Unicode code point 字符数和 UTF-8 字节数描述同一份
        normalized combined 文本。使用 ``wait`` 等待，使用 ``execution_status``
        查看状态，使用 ``read_output`` 或 ``search_output`` 读取或搜索保留的输出。

        Args:
            freeform: 原始 IPython cell 源码文本。

        Returns:
            已接受 execution 的当前状态；完成的小输出直接进入模型内容，
            running 或 token 受限状态给出 ``execution`` 与省略原因。
        """
        return await call("run_cell", ctx, lambda session: session.run_cell(freeform))

    @mcp.tool(output_schema=None)
    async def status(ctx: Context) -> ToolResult:
        """返回 workspace、kernel 与最近执行记录的观察状态。

        状态范围
        --------

        返回当前 IPython 工作台启动时解析的 workspace、其
        ``workspace_resolution`` 来源
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
            当前 IPython 工作台与 kernel 的状态快照。
        """
        return await call("status", ctx, lambda session: session.status())

    @mcp.tool(output_schema=None)
    async def execution_status(ctx: Context, execution: int | None = None) -> ToolResult:
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
            输出总行数、Unicode code point 字符数、UTF-8 字节数、输出省略原因
            与错误摘要。
        """
        return await call("execution_status", ctx, lambda session: session.execution_status(execution))

    @mcp.tool(output_schema=None)
    async def read_output(ctx: Context, execution: int | None = None, stream: str = "combined", line_range: str | None = None, max_chars: int | None = None) -> ToolResult:
        """读取一个 execution 的指定输出流。

        选择与流
        ----------

        ``execution`` 的选择规则与 ``execution_status`` 相同。``stream``
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
        return await call("read_output", ctx, lambda session: session.read_output(execution, stream, line_range, max_chars))

    @mcp.tool(output_schema=None)
    async def search_output(ctx: Context, query: str, execution: int | None = None, stream: str = "combined", query_mode: str = "auto", context_before: int = 0, context_after: int = 0, ignore_case: bool = False, max_chars: int | None = None) -> ToolResult:
        """在一个 execution 的指定输出流中搜索文本或正则表达式。

        选择与匹配
        ------------

        ``execution`` 的选择规则与 ``execution_status`` 相同；可选的
        ``stream`` 值与 ``read_output`` 相同。``query_mode="literal"``
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
        return await call("search_output", ctx, lambda session: session.search_output(query, execution, stream, query_mode, context_before, context_after, ignore_case, max_chars))

    @mcp.tool(output_schema=None, meta=_FULL_BODY_TOOL_META)
    async def wait(ctx: Context, execution: int | None = None, timeout_seconds: float = 30) -> ToolResult:
        """等待一个 execution 结束，或在指定时限到达时返回其当前状态。

        选择与等待
        ----------

        ``execution`` 的选择规则与 ``execution_status`` 相同。等待
        到期只结束本次工具调用，不中断 Python cell。后续可再次调用本工具，
        或用 ``read_output`` 查看已到达的输出。

        完整输出交付
        ------------

        当所选 execution 的 cell 头含 ``--full-output`` 且已达到终态时，本工具
        直接返回完整 combined 正文，不应用默认 5,000-token 省略；仍在运行的
        execution 继续返回 running，而非不完整正文。

        Args:
            execution: 要等待的正整数执行编号。省略时使用当前记录，随后
                使用最近记录。
            timeout_seconds: 本次调用最多等待的正数秒数；默认 30 秒。

        Returns:
            选中 execution 的当前状态和可返回的输出表面。未找到记录或
            非正等待时长返回对应错误。
        """
        return await call("wait", ctx, lambda session: session.wait(execution, timeout_seconds))

    @mcp.tool(output_schema=None)
    async def interrupt(ctx: Context) -> ToolResult:
        """向当前正在运行的 execution 发送 kernel 中断信号。

        中断语义
        --------

        该工具只作用于当前 running 记录。信号已发送不等同于记录已终态：
        kernel 到达 IOPub ``idle`` 后，记录才会报告 ``interrupted``、
        ``error`` 或其他最终状态。kernel 空闲时返回 idle。

        Returns:
            已发送信号时返回目标 ``execution``；kernel 空闲时返回 idle。
        """
        # On Windows an interrupt replaces the kernel process.
        return await call(
            "interrupt",
            ctx,
            lambda session: session.interrupt(),
            replaces_kernel=True,
        )

    @mcp.tool(output_schema=None)
    async def restart(ctx: Context) -> ToolResult:
        """重启 IPython kernel，并保留当前持久 IPython 会话的 execution 历史。

        重置边界
        --------

        若存在 running execution，它会先标记为 ``killed``；随后旧 kernel
        停止并创建替代 kernel。已存在的记录及其输出流仍可按原整数
        ``execution`` 读取，下一次接受的 cell 使用连续的下一个编号。

        Returns:
            新 kernel 的状态与 PID；重启失败时返回 workspace 或 kernel
            启动错误。
        """
        return await call(
            "restart",
            ctx,
            lambda session: session.restart(),
            replaces_kernel=True,
        )

    return mcp
