"""Read what one MCP request says about its session and the kernel it wants."""

from __future__ import annotations

from urllib.parse import unquote

from fastmcp import Context
from fastmcp.server.dependencies import get_http_headers

from loommux.identity import label_problem
from loommux.resource.model import KernelRequest, LeaseClient, RequestMode

RESOURCE_HEADER = "x-loommux-resource"
OPERATOR_HEADER = "x-loommux-operator"
LEASE_POLICY_GENERATION_HEADER = "x-loommux-lease-policy-generation"


class ResourceRoutingError(RuntimeError):
    """A request did not carry enough identity to select a resource."""


def decode_header(name: str) -> str:
    return unquote(get_http_headers().get(name, "")).strip()


def connection_request() -> KernelRequest | None:
    """The main kernel a connection header names for every session it opens."""
    resource_name = decode_header(RESOURCE_HEADER)
    if not resource_name:
        return None
    return KernelRequest(validate_kernel_name(resource_name), RequestMode.JOIN)


def private_kernel_name(client: LeaseClient) -> str:
    """The name of the main kernel opened for a session that asks for none."""
    return f"private-{client.client_id[:8]}"


def validate_kernel_name(name: str) -> str:
    name = _validate_display_label(name, "kernel name")
    if "/" in name:
        raise ResourceRoutingError("kernel name must not contain '/', which separates a main kernel from its child kernel in a path")
    return name


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
    if (problem := label_problem(value, label)) is not None:
        raise ResourceRoutingError(problem)
    return value
