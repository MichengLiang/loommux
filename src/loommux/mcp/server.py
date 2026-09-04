"""Provide the MCP server factory and installed command entrypoint."""

from __future__ import annotations

from collections.abc import Sequence

from fastmcp import FastMCP

from loommux.mcp.entrypoints import run_entrypoint
from loommux.mcp.factory import create_mcp as create_factory


def create_mcp() -> FastMCP:
    """Create the default content-only MCP server."""

    return create_factory("content")


mcp = create_mcp()


def main(argv: Sequence[str] | None = None) -> None:
    """Run the MCP consumer using its command-line transport selection."""

    run_entrypoint(lambda result_mode: create_factory(result_mode), argv=argv)


if __name__ == "__main__":
    main()
