from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from typing import Literal

from fastmcp import FastMCP

from loommux.mcp_result_policy import ResultChannelPolicy

Transport = Literal["stdio", "streamable-http"]
ResultMode = Literal["structured", "content"]
ServerFactory = Callable[[ResultChannelPolicy], FastMCP]


def run_entrypoint(
    create_server: ServerFactory,
    default_policy: ResultChannelPolicy,
    default_transport: Transport,
    *,
    configure_workspace: Callable[[], None] | None = None,
    default_host: str = "127.0.0.1",
    default_port: int = 8801,
    default_path: str = "/mcp",
    argv: Sequence[str] | None = None,
) -> None:
    """Run one result policy over an independently selected MCP transport."""
    parser = argparse.ArgumentParser(description="Run the loommux IPython MCP server.")
    parser.add_argument("--result-mode", choices=("structured", "content"), default=_result_mode(default_policy), help="Return content plus structuredContent, or content blocks only.")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default=default_transport, help="Use a subprocess stdio connection or a Streamable HTTP service.")
    parser.add_argument("--host", default=default_host, help="HTTP bind host; ignored by stdio.")
    parser.add_argument("--port", type=_port, default=default_port, help="HTTP bind port; ignored by stdio.")
    parser.add_argument("--path", type=_path, default=default_path, help="Streamable HTTP route; ignored by stdio.")
    options = parser.parse_args(argv)
    policy = _result_policy(options.result_mode)

    if configure_workspace is not None:
        configure_workspace()

    mcp = create_server(policy)
    if options.transport == "stdio":
        mcp.run(transport="stdio")
        return
    mcp.run(transport="streamable-http", host=options.host, port=options.port, path=options.path)


def _result_mode(policy: ResultChannelPolicy) -> ResultMode:
    return "structured" if policy == "dual_channel" else "content"


def _result_policy(mode: ResultMode) -> ResultChannelPolicy:
    return "dual_channel" if mode == "structured" else "content_only"


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be in the range 1 through 65535")
    return port


def _path(value: str) -> str:
    if not value.startswith("/"):
        raise argparse.ArgumentTypeError("path must start with '/'")
    return value
