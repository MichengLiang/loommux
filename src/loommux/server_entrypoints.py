from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from typing import cast

from fastmcp import FastMCP

from loommux.mcp_result_policy import ResultMode

ServerFactory = Callable[[ResultMode], FastMCP]


def run_entrypoint(
    create_server: ServerFactory,
    *,
    default_host: str = "127.0.0.1",
    default_port: int = 8801,
    default_path: str = "/mcp",
    argv: Sequence[str] | None = None,
) -> None:
    """Run the sole loommux command surface over Studio stdio or HTTP."""
    parser = argparse.ArgumentParser(description="Run the loommux IPython MCP server.")
    parser.add_argument("--result-mode", choices=("structured",), default="content", help="Explicitly include structuredContent alongside content.")
    parser.add_argument("--server", action="store_true", help="Start a Streamable HTTP MCP service instead of using Studio subprocess stdio.")
    parser.add_argument("--host", default=default_host, help="HTTP bind host; ignored by stdio.")
    parser.add_argument("--port", type=_port, default=default_port, help="HTTP bind port; ignored by stdio.")
    parser.add_argument("--path", type=_path, default=default_path, help="Streamable HTTP route; ignored by stdio.")
    options = parser.parse_args(argv)
    mcp = create_server(cast(ResultMode, options.result_mode))
    if not options.server:
        mcp.run(transport="stdio")
        return
    mcp.run(transport="streamable-http", host=options.host, port=options.port, path=options.path)


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
