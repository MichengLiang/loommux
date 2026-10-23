"""Select the MCP transport and launch the already-built consumer."""

from __future__ import annotations

import argparse
import faulthandler
import signal
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from fastmcp import FastMCP

from loommux.config import ConfigError, LoommuxConfig, find_config, load_config
from loommux.identity import loopback_control_url
from loommux.mcp.result import ResultMode

# The second argument is the HTTP origin kernels use to reach this daemon's
# control plane, or None for stdio, which serves none; the third is what the
# configuration directory says, or the defaults without one.
ServerFactory = Callable[[ResultMode, str | None, LoommuxConfig], FastMCP]


def run_entrypoint(
    create_server: ServerFactory,
    *,
    default_host: str = "127.0.0.1",
    default_port: int = 8801,
    default_path: str = "/mcp",
    argv: Sequence[str] | None = None,
) -> None:
    """Run the MCP consumer over Studio stdio or HTTP."""
    parser = argparse.ArgumentParser(description="Run the loommux IPython MCP server.")
    parser.add_argument("--result-mode", choices=("structured",), default="content", help="Explicitly include structuredContent alongside content.")
    parser.add_argument("--server", action="store_true", help="Start a Streamable HTTP MCP service instead of using Studio subprocess stdio.")
    parser.add_argument("--host", default=default_host, help="HTTP bind host; ignored by stdio.")
    parser.add_argument("--port", type=_port, default=default_port, help="HTTP bind port; ignored by stdio.")
    parser.add_argument("--path", type=_path, default=default_path, help="Streamable HTTP route; ignored by stdio.")
    parser.add_argument(
        "--config",
        metavar="DIR",
        help="Configuration directory (config.py for settings and resolve_workspace, startup/ for files every kernel runs when it starts); without it, the nearest .loommux at or above the current directory.",
    )
    options = parser.parse_args(argv)
    if options.config is None:
        directory, how = find_config(Path.cwd()), "found from the launch directory"
    else:
        directory, how = Path(options.config).expanduser().resolve(), "named by --config"
    try:
        config = LoommuxConfig() if directory is None else load_config(directory)
    except ConfigError as exc:
        # Before any transport opens, so a host sees the server fail to start
        # rather than tools that run on settings nobody chose.
        parser.exit(2, f"loommux: {exc.status}: {exc}\n")
    # stderr, because under stdio the standard output is the MCP channel.
    print(f"loommux: configuration {directory} ({how})" if directory else "loommux: no configuration directory; defaults apply", file=sys.stderr)
    if sys.platform != "win32":
        # A server whose event loop is stuck never runs uvicorn's Ctrl+C and
        # SIGTERM handling, and finding out where it is stuck starts with its
        # stacks. faulthandler prints every thread's stack from a C-level signal
        # handler, so `kill -USR1 <pid>` works while the event loop is frozen and
        # needs none of the ptrace privileges py-spy or gdb require.
        faulthandler.register(signal.SIGUSR1, all_threads=True)
    result_mode = cast(ResultMode, options.result_mode)
    if not options.server:
        create_server(result_mode, None, config).run(transport="stdio")
        return
    mcp = create_server(result_mode, loopback_control_url(options.host, options.port), config)
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
    if value == "/" or value == "/api" or value.startswith("/api/"):
        raise argparse.ArgumentTypeError("path conflicts with the HTTP control plane")
    return value
