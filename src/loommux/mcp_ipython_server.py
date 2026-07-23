from __future__ import annotations

from collections.abc import Sequence

from fastmcp import FastMCP

from loommux.mcp_server_factory import create_mcp as create_server
from loommux.monitoring import MonitorPublisher
from loommux.server_entrypoints import run_entrypoint


def create_mcp(monitor_publisher: MonitorPublisher | None = None) -> FastMCP:
    return create_server("content", monitor_publisher)


mcp = create_mcp()


def main(argv: Sequence[str] | None = None) -> None:
    run_entrypoint(lambda result_mode: create_server(result_mode), argv=argv)


if __name__ == "__main__":
    main()
