"""Install the HTTP-only operational surface for kernel resources."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from loommux.resource import (
    KernelResourceManager,
    LeaseMode,
    ResourceBusyError,
    ResourceManagerError,
    ResourceNotFoundError,
)

CONSOLE_PATH = Path(__file__).with_name("console.html")


def install_control_routes(
    mcp: FastMCP,
    get_manager: Callable[[], KernelResourceManager | None],
) -> None:
    """Keep server operations outside the model-facing eight-tool surface."""

    def manager() -> KernelResourceManager:
        value = get_manager()
        if value is None:
            raise ResourceManagerError("resource manager is not started")
        return value

    @mcp.custom_route("/", methods=["GET"], include_in_schema=False)
    async def console_page(_: Request) -> Response:
        return HTMLResponse(CONSOLE_PATH.read_text(encoding="utf-8"))

    @mcp.custom_route("/api/resources", methods=["GET"])
    async def list_resources(_: Request) -> Response:
        selected = manager()
        return JSONResponse(
            {
                "resources": await selected.snapshot(),
                "policy": (await selected.policy_manager.current()).as_dict(),
            }
        )

    @mcp.custom_route("/api/lease-policy", methods=["GET"])
    async def get_policy(_: Request) -> Response:
        return JSONResponse(
            {"policy": (await manager().policy_manager.current()).as_dict()}
        )

    @mcp.custom_route("/api/lease-policy", methods=["PUT"])
    async def update_policy(request: Request) -> Response:
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("request body must be a JSON object")
            selected = manager()
            previous = await selected.policy_manager.current()
            policy = await selected.policy_manager.update(
                mode=LeaseMode(str(body.get("mode", previous.mode.value))),
                private_activity_timeout_seconds=_number(
                    body,
                    "private_activity_timeout_seconds",
                    previous.private_activity_timeout_seconds,
                ),
                named_activity_timeout_seconds=_number(
                    body,
                    "named_activity_timeout_seconds",
                    previous.named_activity_timeout_seconds,
                ),
                heartbeat_interval_seconds=_number(
                    body,
                    "heartbeat_interval_seconds",
                    previous.heartbeat_interval_seconds,
                ),
                heartbeat_timeout_seconds=_number(
                    body,
                    "heartbeat_timeout_seconds",
                    previous.heartbeat_timeout_seconds,
                ),
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return JSONResponse(
                {"ok": False, "message": str(exc)},
                status_code=400,
            )
        return JSONResponse(
            {
                "ok": True,
                "changed": policy.generation != previous.generation,
                "policy": policy.as_dict(),
            }
        )

    @mcp.custom_route("/api/resources/{resource_id}/interrupt", methods=["POST"])
    async def interrupt_resource(request: Request) -> Response:
        return await _resource_action(
            lambda: manager().interrupt(request.path_params["resource_id"])
        )

    @mcp.custom_route("/api/resources/{resource_id}/restart", methods=["POST"])
    async def restart_resource(request: Request) -> Response:
        return await _resource_action(
            lambda: manager().restart(request.path_params["resource_id"])
        )

    @mcp.custom_route("/api/resources/{resource_id}/recycle", methods=["POST"])
    async def recycle_resource(request: Request) -> Response:
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        force = bool(body.get("force", False)) if isinstance(body, dict) else False
        try:
            resource = await manager().recycle(
                request.path_params["resource_id"],
                force=force,
                reason="recycled from HTTP control plane",
            )
        except (ResourceBusyError, ResourceNotFoundError) as exc:
            return JSONResponse(
                {"ok": False, "message": str(exc)},
                status_code=409,
            )
        return JSONResponse(
            {
                "ok": True,
                "message": f"recycled {resource.address.display_name}",
            }
        )

    @mcp.custom_route("/api/resources/recycle-idle", methods=["POST"])
    async def recycle_idle(_: Request) -> Response:
        count = await manager().recycle_idle()
        return JSONResponse({"ok": True, "count": count})

    @mcp.custom_route("/api/resources/recycle-all", methods=["POST"])
    async def recycle_all(_: Request) -> Response:
        count = await manager().recycle_all()
        return JSONResponse({"ok": True, "count": count})


async def _resource_action(
    operation: Callable[[], Any],
) -> JSONResponse:
    try:
        result = await operation()
    except ResourceNotFoundError as exc:
        return JSONResponse(
            {"ok": False, "message": str(exc)},
            status_code=404,
        )
    return JSONResponse({"ok": True, "result": result})


def _number(
    body: dict[str, Any],
    name: str,
    default: float,
) -> float:
    value = body.get(name, default)
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    return float(value)
