"""Attach client lease renewal to the standard MCP ping request."""

from __future__ import annotations

from collections.abc import Callable

import mcp.types as mcp_types
from fastmcp import Context, FastMCP

from loommux.resource import KernelResourceManager, resolve_address, resolve_client


def install_lease_aware_ping_handler(
    mcp: FastMCP,
    get_manager: Callable[[], KernelResourceManager | None],
) -> None:
    """Preserve the protocol response and renew only an existing client lease."""

    low_level_server = mcp._mcp_server
    original_handler = low_level_server.request_handlers[mcp_types.PingRequest]

    async def lease_aware_ping_handler(
        request: mcp_types.PingRequest,
    ) -> mcp_types.ServerResult:
        result = await original_handler(request)
        manager = get_manager()
        if manager is None:
            return result
        request_context = low_level_server.request_context
        ctx = Context(mcp, session=request_context.session)
        address = resolve_address(ctx)
        client = resolve_client(ctx)
        await manager.renew_client_lease(address.key, client.client_id)
        return result

    low_level_server.request_handlers[mcp_types.PingRequest] = (
        lease_aware_ping_handler
    )
