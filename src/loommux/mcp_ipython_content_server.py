from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path

from fastmcp import FastMCP

from loommux.host_workspace_config import WORKSPACE_CONFIG_ENV
from loommux.mcp_server_factory import create_mcp as create_server
from loommux.monitoring import MonitorPublisher


def create_mcp(monitor_publisher: MonitorPublisher | None = None) -> FastMCP:
    return create_server("content_only", monitor_publisher)


mcp = create_mcp()


def configure_manual_content_workspace(environ: MutableMapping[str, str] | None = None) -> None:
    """Select the bundled Codex resolver for the manually started HTTP host."""
    # A caller-provided resolver remains the only higher-priority host authority.
    environment = os.environ if environ is None else environ
    environment.setdefault(WORKSPACE_CONFIG_ENV, str(Path(__file__).with_name("codex_workspace_resolver.py").resolve()))


def main() -> None:
    configure_manual_content_workspace()
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8801)


if __name__ == "__main__":
    main()
