"""Resolve one MCP request to a private or explicitly named resource."""

from __future__ import annotations

from urllib.parse import unquote

from fastmcp import Context
from fastmcp.server.dependencies import get_http_headers

from loommux.resource.model import LeaseClient, ResourceAddress

RESOURCE_HEADER = "x-loommux-resource"
OPERATOR_HEADER = "x-loommux-operator"
LEASE_POLICY_GENERATION_HEADER = "x-loommux-lease-policy-generation"
MAX_DISPLAY_LABEL_LENGTH = 256


class ResourceRoutingError(RuntimeError):
    """A request did not carry enough identity to select a resource."""


def decode_header(name: str) -> str:
    return unquote(get_http_headers().get(name, "")).strip()


def resolve_address(ctx: Context) -> ResourceAddress:
    resource_name = decode_header(RESOURCE_HEADER)
    if resource_name:
        resource_name = _validate_display_label(resource_name, "resource name")
        return ResourceAddress(
            key=f"named:{resource_name}",
            display_name=resource_name,
            scope="named_shared",
            shared=True,
        )

    session_id = ctx.session_id
    if not session_id:
        raise ResourceRoutingError("MCP session identity is unavailable")
    return ResourceAddress(
        key=f"session:{session_id}",
        display_name=f"private-{session_id[:8]}",
        scope="session_private",
        shared=False,
    )


def resolve_client(ctx: Context) -> LeaseClient:
    session_id = ctx.session_id
    if not session_id:
        raise ResourceRoutingError("MCP session identity is unavailable")
    operator = decode_header(OPERATOR_HEADER)
    return LeaseClient(
        client_id=session_id,
        display_name=(
            _validate_display_label(operator, "operator")
            if operator
            else f"client-{session_id[:8]}"
        ),
    )


def resolve_policy_generation() -> int | None:
    raw_generation = decode_header(LEASE_POLICY_GENERATION_HEADER)
    if not raw_generation:
        return None
    try:
        generation = int(raw_generation)
    except ValueError as exc:
        raise ResourceRoutingError("lease policy generation must be a positive integer") from exc
    if generation <= 0:
        raise ResourceRoutingError("lease policy generation must be a positive integer")
    return generation


def _validate_display_label(value: str, label: str) -> str:
    if len(value) > MAX_DISPLAY_LABEL_LENGTH:
        raise ResourceRoutingError(
            f"{label} must not exceed {MAX_DISPLAY_LABEL_LENGTH} characters"
        )
    if not value.isprintable():
        raise ResourceRoutingError(f"{label} must contain printable characters only")
    return value
