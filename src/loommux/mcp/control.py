"""Install the HTTP-only operational surface for kernel resources."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from loommux.resource import (
    KernelNotFoundError,
    KernelResourceManager,
    LeaseMode,
    ResourceBusyError,
    ResourceManagerError,
    ResourceNotFoundError,
    ResourceRoutingError,
)
from loommux.session import IPythonSession

CONSOLE_PATH = Path(__file__).with_name("console.html")
# A session answers ok=False for an execution that ended in error; that is the
# requested record, not a failed request.
_EXECUTION_STATES = {"running", "completed", "error", "interrupted", "killed"}


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
        """Every kernel, oldest first."""
        selected = manager()
        return JSONResponse(
            {
                "resources": await selected.snapshot(),
                "policy": (await selected.policy_manager.current()).as_dict(),
            }
        )

    @mcp.custom_route("/api/resources", methods=["POST"])
    async def create_resource(request: Request) -> Response:
        """The kernel ``path`` names from the kernel ``context``, opening a child kernel when it is missing."""
        body, error = await _json_object(request)
        if error is not None:
            return error
        path, context = body.get("path"), body.get("context")
        if not isinstance(path, str) or not (context is None or isinstance(context, str)):
            return JSONResponse({"ok": False, "message": "path must be a string and context a string or null"}, status_code=400)
        return await _registry_answer(lambda selected: selected.create(path, context))

    # Registered before /api/resources/{resource_id}, which would read "lookup" as an id.
    @mcp.custom_route("/api/resources/lookup", methods=["GET"])
    async def lookup_resource(request: Request) -> Response:
        """The kernel ``?path=`` names from the kernel ``?context=``; opens nothing."""
        path = request.query_params.get("path")
        if not path:
            return JSONResponse({"ok": False, "message": "path is required"}, status_code=400)
        return await _registry_answer(lambda selected: selected.lookup(path, request.query_params.get("context") or None))

    async def _registry_answer(operation: Callable[[KernelResourceManager], Awaitable[Any]]) -> JSONResponse:
        selected = manager()
        try:
            resource = await operation(selected)
            # The kernel may be retired between resolving and describing it,
            # for example a child kernel whose main kernel was just let go of;
            # that is the same answer as not finding it.
            snapshot = await selected.snapshot_one(resource.resource_id)
        except ResourceRoutingError as exc:
            return JSONResponse({"ok": False, "status": "invalid_kernel_path", "message": str(exc)}, status_code=400)
        except (KernelNotFoundError, ResourceNotFoundError) as exc:
            return JSONResponse({"ok": False, "status": "kernel_not_found", "message": str(exc)}, status_code=404)
        return JSONResponse({"ok": True, "resource": snapshot})

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
                mode=LeaseMode(
                    str(body.get("mode", previous.mode.value)).strip().lower()
                ),
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
                connection_grace_seconds=_number(
                    body,
                    "connection_grace_seconds",
                    previous.connection_grace_seconds,
                ),
            )
        except (
            ValueError,
            TypeError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as exc:
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

    @mcp.custom_route("/api/resources/{resource_id}", methods=["GET"])
    async def get_resource(request: Request) -> Response:
        try:
            snapshot = await manager().snapshot_one(request.path_params["resource_id"])
        except ResourceNotFoundError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=404)
        return JSONResponse({"ok": True, "resource": snapshot})

    @mcp.custom_route("/api/resources/{resource_id}/executions", methods=["POST"])
    async def submit_cell(request: Request) -> Response:
        body, error = await _json_object(request)
        if error is not None:
            return error
        source, wait_seconds, silence = body.get("source"), body.get("wait_seconds", 10), body.get("silence_seconds")
        if not isinstance(source, str) or not _is_number(wait_seconds) or not (silence is None or _is_number(silence)):
            return JSONResponse({"ok": False, "message": "source must be a string, and wait_seconds and silence_seconds numbers"}, status_code=400)
        return await _session_call(request, lambda session: session.run_cell(source, wait_seconds=float(wait_seconds), silence_seconds=None if silence is None else float(silence)))

    @mcp.custom_route("/api/resources/{resource_id}/rename", methods=["POST"])
    async def rename_resource(request: Request) -> Response:
        """Rename a kernel; the response's resource carries the name it received."""
        body, error = await _json_object(request)
        if error is not None:
            return error
        name = body.get("name")
        if not isinstance(name, str):
            return JSONResponse({"ok": False, "message": "name must be a string"}, status_code=400)
        selected = manager()
        try:
            resource = await selected.rename(request.path_params["resource_id"], name)
            # Retired between renaming and describing: not found, as above.
            snapshot = await selected.snapshot_one(resource.resource_id)
        except ResourceRoutingError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
        except ResourceNotFoundError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=404)
        return JSONResponse({"ok": True, "resource": snapshot})

    @mcp.custom_route("/api/resources/{resource_id}/executions/{execution}", methods=["GET"])
    async def execution_status(request: Request) -> Response:
        try:
            execution = _execution(request)
        except ValueError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
        return await _session_call(request, lambda session: session.execution_status(execution))

    @mcp.custom_route("/api/resources/{resource_id}/executions/{execution}/wait", methods=["POST"])
    async def wait_execution(request: Request) -> Response:
        body, error = await _json_object(request)
        if error is not None:
            return error
        timeout, silence = body.get("timeout_seconds", 30), body.get("silence_seconds")
        try:
            execution = _execution(request)
            if not _is_number(timeout) or not (silence is None or _is_number(silence)):
                raise ValueError("timeout_seconds and silence_seconds must be numbers")
        except ValueError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
        return await _session_call(request, lambda session: session.wait(execution, float(timeout), None if silence is None else float(silence)))

    @mcp.custom_route("/api/resources/{resource_id}/executions/{execution}/output", methods=["GET"])
    async def read_output(request: Request) -> Response:
        query = request.query_params
        try:
            execution, max_chars = _execution(request), _optional_int(query, "max_chars")
        except ValueError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
        stream, line_range = query.get("stream", "combined"), query.get("line_range")
        return await _session_call(request, lambda session: session.read_output(execution, stream, line_range, max_chars))

    @mcp.custom_route("/api/resources/{resource_id}/executions/{execution}/search", methods=["GET"])
    async def search_output(request: Request) -> Response:
        query = request.query_params
        text = query.get("query")
        try:
            if text is None:
                raise ValueError("query is required")
            execution, max_chars = _execution(request), _optional_int(query, "max_chars")
            before, after = _optional_int(query, "context_before") or 0, _optional_int(query, "context_after") or 0
        except ValueError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)
        stream, mode, ignore_case = query.get("stream", "combined"), query.get("query_mode", "auto"), query.get("ignore_case") == "true"
        return await _session_call(
            request,
            lambda session: session.search_output(
                text, execution, stream, query_mode=mode, context_before=before, context_after=after, ignore_case=ignore_case, max_chars=max_chars
            ),
        )

    @mcp.custom_route("/api/resources/{resource_id}/executions/{execution:int}/combined", methods=["GET"])
    async def follow_output(request: Request) -> Response:
        """Combined output after character ``since``; returns early when new text arrives."""
        execution = request.path_params["execution"]
        try:
            since = int(request.query_params.get("since", "0"))
            wait = float(request.query_params.get("wait", "0"))
        except ValueError:
            return JSONResponse({"ok": False, "message": "since must be an integer and wait a number"}, status_code=400)
        return await _session_call(request, lambda session: session.output_since(execution, since, wait))

    async def _session_call(request: Request, operation: Callable[[IPythonSession], dict[str, Any]]) -> JSONResponse:
        try:
            async with manager().program_operation(request.path_params["resource_id"]) as resource:
                result = await asyncio.to_thread(operation, resource.session)
        except ResourceNotFoundError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=404)
        except ResourceManagerError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=409)
        public = {key: value for key, value in result.items() if not key.startswith("_")}
        if public.get("ok") is False and public.get("status") not in _EXECUTION_STATES:
            return JSONResponse({"ok": False, "status": public.get("status"), "message": public.get("message"), "result": public}, status_code=409)
        return JSONResponse({"ok": True, "result": public})

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

    @mcp.custom_route("/api/resources/{resource_id}/health", methods=["POST"])
    async def health_resource(request: Request) -> Response:
        return await _resource_action(
            lambda: manager().health(request.path_params["resource_id"])
        )

    @mcp.custom_route("/api/resources/{resource_id}/recycle", methods=["POST"])
    async def recycle_resource(request: Request) -> Response:
        body, error = await _json_object(request, optional=True)
        if error is not None:
            return error
        force = body.get("force", False)
        if not isinstance(force, bool):
            return JSONResponse(
                {"ok": False, "message": "force must be a boolean"},
                status_code=400,
            )
        try:
            resource = await manager().recycle(
                request.path_params["resource_id"],
                force=force,
                reason="recycled from HTTP control plane",
            )
        except ResourceNotFoundError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=404)
        except ResourceBusyError as exc:
            return JSONResponse(
                {"ok": False, "message": str(exc)},
                status_code=409,
            )
        return JSONResponse(
            {
                "ok": True,
                "message": f"recycled {resource.name}",
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
    operation: Callable[[], Awaitable[dict[str, Any]]],
) -> JSONResponse:
    try:
        result = await operation()
    except ResourceNotFoundError as exc:
        return JSONResponse(
            {"ok": False, "message": str(exc)},
            status_code=404,
        )
    except ResourceManagerError as exc:
        return JSONResponse(
            {"ok": False, "message": str(exc)},
            status_code=409,
        )
    succeeded = result.get("ok") is not False
    return JSONResponse(
        {"ok": succeeded, "result": result},
        status_code=200 if succeeded else 409,
    )


async def _json_object(request: Request, *, optional: bool = False) -> tuple[dict[str, Any], JSONResponse | None]:
    """The request's JSON object body, or the 400 response to return instead.

    ``optional`` reads an empty body as ``{}``, for an action whose every
    option has a default and that, like interrupt or restart, needs no body.
    """
    raw = await request.body()
    if optional and not raw.strip():
        return {}, None
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict):
        return {}, JSONResponse({"ok": False, "message": "request body must be a JSON object"}, status_code=400)
    return body, None


def _execution(request: Request) -> int | None:
    """The execution a path names: a number, or ``latest`` for the current then the most recent, as the tools' ``None``."""
    value = request.path_params["execution"]
    if value == "latest":
        return None
    if not value.isdecimal():
        raise ValueError("execution must be a number or latest")
    return int(value)


def _optional_int(query: Any, name: str) -> int | None:  # noqa: ANN401
    value = query.get(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _number(
    body: dict[str, Any],
    name: str,
    default: float,
) -> float:
    value = body.get(name, default)
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    return float(value)
