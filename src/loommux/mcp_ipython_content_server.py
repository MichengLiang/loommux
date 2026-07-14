from __future__ import annotations

import os
from collections.abc import MutableMapping, Sequence
from pathlib import Path

from fastmcp import FastMCP

from loommux.host_workspace_config import WORKSPACE_CONFIG_ENV
from loommux.mcp_server_factory import create_mcp as create_server
from loommux.monitoring import MonitorPublisher
from loommux.server_entrypoints import run_entrypoint


def create_mcp(monitor_publisher: MonitorPublisher | None = None) -> FastMCP:
    return create_server("content_only", monitor_publisher)


mcp = create_mcp()


def configure_manual_content_workspace(environ: MutableMapping[str, str] | None = None) -> None:
    """Select the bundled Codex resolver for the manually started content entrypoint."""
    # A caller-provided resolver remains the only higher-priority host authority.
    environment = os.environ if environ is None else environ
    environment.setdefault(WORKSPACE_CONFIG_ENV, str(Path(__file__).with_name("codex_workspace_resolver.py").resolve()))


def main(argv: Sequence[str] | None = None) -> None:
    run_entrypoint(
        lambda policy: create_server(policy),
        "content_only",
        "streamable-http",
        configure_workspace=configure_manual_content_workspace,
        default_host="0.0.0.0",
        argv=argv,
    )


if __name__ == "__main__":
    main()
