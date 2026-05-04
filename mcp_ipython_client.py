from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport


async def main() -> None:
    project_dir = Path(__file__).resolve().parent
    server_file = project_dir / "src" / "loommux" / "mcp_ipython_server.py"
    transport = StdioTransport(command=sys.executable, args=[str(server_file)], cwd=str(project_dir), keep_alive=False)
    async with Client(transport) as client:
        tools = await client.list_tools()
        print("tools:", ", ".join(tool.name for tool in tools))


if __name__ == "__main__":
    asyncio.run(main())
