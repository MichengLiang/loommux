"""Expose the protocol-specific MCP consumer for the loommux runtime."""

from loommux.mcp.server import create_mcp, main, mcp

__all__ = ["create_mcp", "main", "mcp"]
