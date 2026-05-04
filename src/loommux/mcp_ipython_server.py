from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP

from loommux.adapter import IPythonMCPAdapter


def create_mcp() -> FastMCP:
    adapter = IPythonMCPAdapter()

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        try:
            yield {"adapter": adapter}
        finally:
            adapter.close()

    mcp = FastMCP("loommux IPython MCP adapter", lifespan=lifespan)

    @mcp.tool
    def set_workspace(path: str) -> dict[str, Any]:
        return adapter.set_workspace(path)

    @mcp.tool
    def run_python(code: str, timeout_seconds: float = 30) -> dict[str, Any]:
        return adapter.run_python(code, timeout_seconds)

    @mcp.tool
    def python_status() -> dict[str, Any]:
        return adapter.python_status()

    @mcp.tool
    def read_python_output(execution_id: str | None = None) -> dict[str, Any]:
        return adapter.read_python_output(execution_id)

    @mcp.tool
    def wait_python(execution_id: str | None = None, timeout_seconds: float = 30) -> dict[str, Any]:
        return adapter.wait_python(execution_id, timeout_seconds)

    @mcp.tool
    def interrupt_python() -> dict[str, Any]:
        return adapter.interrupt_python()

    @mcp.tool
    def reset_python() -> dict[str, Any]:
        return adapter.reset_python()

    return mcp


mcp = create_mcp()


if __name__ == "__main__":
    mcp.run()
