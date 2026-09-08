"""Lease-aware Streamable HTTP client for a loommux kernel resource."""

from __future__ import annotations

import asyncio
import contextlib
import datetime
from dataclasses import dataclass
from math import isfinite
from types import TracebackType
from typing import Any, Literal, overload
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from fastmcp import Client
from fastmcp.client.client import CallToolResult, ProgressHandler
from fastmcp.client.tasks import ToolTask
from fastmcp.client.transports import StreamableHttpTransport
from mcp.types import Tool as MCPTool

from loommux.resource import (
    LEASE_POLICY_GENERATION_HEADER,
    OPERATOR_HEADER,
    RESOURCE_HEADER,
)


@dataclass(frozen=True)
class RemoteLeasePolicy:
    """The immutable policy generation selected before MCP initialization."""

    mode: str
    generation: int
    private_activity_timeout_seconds: float
    named_activity_timeout_seconds: float
    heartbeat_interval_seconds: float
    heartbeat_timeout_seconds: float

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RemoteLeasePolicy:
        policy = cls(
            mode=str(payload["mode"]),
            generation=int(payload["generation"]),
            private_activity_timeout_seconds=float(payload["private_activity_timeout_seconds"]),
            named_activity_timeout_seconds=float(payload["named_activity_timeout_seconds"]),
            heartbeat_interval_seconds=float(payload["heartbeat_interval_seconds"]),
            heartbeat_timeout_seconds=float(payload["heartbeat_timeout_seconds"]),
        )
        if policy.mode not in {"activity", "heartbeat"} or policy.generation <= 0:
            raise ValueError("server returned an invalid lease policy identity")
        durations = (
            policy.private_activity_timeout_seconds,
            policy.named_activity_timeout_seconds,
            policy.heartbeat_interval_seconds,
            policy.heartbeat_timeout_seconds,
        )
        if any(not isfinite(value) or value <= 0 for value in durations):
            raise ValueError("server returned an invalid lease duration")
        if policy.heartbeat_timeout_seconds <= policy.heartbeat_interval_seconds:
            raise ValueError("server returned an invalid heartbeat window")
        return policy


class LeaseAwareClient:
    """Pin one server policy generation and own its heartbeat task."""

    def __init__(
        self,
        server_url: str,
        operator: str | None = None,
        *,
        resource_name: str | None = None,
        control_url: str | None = None,
    ) -> None:
        self.server_url = server_url
        self.control_url = control_url or _control_origin(server_url)
        self.operator = operator.strip() if operator is not None else None
        self.resource_name = resource_name.strip() if resource_name else None
        self.policy: RemoteLeasePolicy | None = None
        self.heartbeat_count = 0
        self.last_heartbeat_error: str | None = None
        self._client: Client | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> LeaseAwareClient:
        if self._client is not None:
            raise RuntimeError("client context is already active")
        self.heartbeat_count = 0
        self.last_heartbeat_error = None
        self.policy = await self._fetch_policy()
        headers = {
            LEASE_POLICY_GENERATION_HEADER: str(self.policy.generation),
        }
        if self.operator:
            headers[OPERATOR_HEADER] = quote(self.operator, safe="")
        if self.resource_name:
            headers[RESOURCE_HEADER] = quote(self.resource_name, safe="")
        self._client = Client(StreamableHttpTransport(self.server_url, headers=headers))
        try:
            await self._client.__aenter__()
        except BaseException:
            client, self._client = self._client, None
            # Cleanup must not replace the connection failure or cancellation
            # that explains why the context was never established.
            with contextlib.suppress(Exception):
                await client.close()
            raise
        if self.policy.mode == "heartbeat":
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(),
                name="loommux-client-heartbeat",
            )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)
            self._heartbeat_task = None
        client, self._client = self._client, None
        if client is None:
            return None
        return await client.__aexit__(exc_type, exc, traceback)

    @overload
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        version: str | None = None,
        timeout: datetime.timedelta | float | int | None = None,
        progress_handler: ProgressHandler | None = None,
        raise_on_error: bool = True,
        meta: dict[str, Any] | None = None,
        task: Literal[False] = False,
        task_id: str | None = None,
        ttl: int = 60000,
    ) -> CallToolResult: ...

    @overload
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        version: str | None = None,
        timeout: datetime.timedelta | float | int | None = None,
        progress_handler: ProgressHandler | None = None,
        raise_on_error: bool = True,
        meta: dict[str, Any] | None = None,
        task: Literal[True],
        task_id: str | None = None,
        ttl: int = 60000,
    ) -> ToolTask: ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        version: str | None = None,
        timeout: datetime.timedelta | float | int | None = None,
        progress_handler: ProgressHandler | None = None,
        raise_on_error: bool = True,
        meta: dict[str, Any] | None = None,
        task: bool = False,
        task_id: str | None = None,
        ttl: int = 60000,
    ) -> CallToolResult | ToolTask:
        """Call a tool through the connected FastMCP client.

        Lease awareness decorates the connection lifecycle; it does not narrow
        the ordinary FastMCP call surface. Keeping these options aligned with
        ``Client.call_tool()`` lets discovery-based hosts use this client
        without reaching through to its private transport owner.
        """
        client = self._require_client()
        if task:
            return await client.call_tool(
                name,
                arguments,
                version=version,
                timeout=timeout,
                progress_handler=progress_handler,
                raise_on_error=raise_on_error,
                meta=meta,
                task=True,
                task_id=task_id,
                ttl=ttl,
            )
        return await client.call_tool(
            name,
            arguments,
            version=version,
            timeout=timeout,
            progress_handler=progress_handler,
            raise_on_error=raise_on_error,
            meta=meta,
            task=False,
        )

    async def list_tools(self, max_pages: int = 250) -> list[MCPTool]:
        """Discover tools through the lease-bound MCP session."""
        return await self._require_client().list_tools(max_pages=max_pages)

    async def ping(self) -> bool:
        return await self._require_client().ping()

    def _require_client(self) -> Client:
        """Return the active transport owner through one lifecycle boundary."""
        if self._client is None:
            raise RuntimeError("client is not inside its async context")
        return self._client

    async def _fetch_policy(self) -> RemoteLeasePolicy:
        async with httpx.AsyncClient(timeout=5) as http:
            response = await http.get(f"{self.control_url.rstrip('/')}/api/lease-policy")
            response.raise_for_status()
        return RemoteLeasePolicy.from_payload(response.json()["policy"])

    async def _heartbeat_loop(self) -> None:
        assert self.policy is not None
        while True:
            await asyncio.sleep(self.policy.heartbeat_interval_seconds)
            try:
                if not await self.ping():
                    raise RuntimeError("server returned a non-standard ping response")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_heartbeat_error = f"{type(exc).__name__}: {exc}"
            else:
                self.heartbeat_count += 1
                self.last_heartbeat_error = None


def _control_origin(server_url: str) -> str:
    parts = urlsplit(server_url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", "")).rstrip("/")
