from __future__ import annotations

from fastmcp import FastMCP

from loommux.mcp_server_factory import create_mcp as create_server
from loommux.monitoring import MonitorPublisher


def create_mcp(monitor_publisher: MonitorPublisher | None = None) -> FastMCP:
    return create_server("dual_channel", monitor_publisher)


mcp = create_mcp()


if __name__ == "__main__":
    mcp.run()
