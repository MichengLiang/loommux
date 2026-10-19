"""Provision, register, observe, and retire logical IPython resources."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from time import monotonic
from typing import Any

from loommux.resource.connections import ConnectionRegistry
from loommux.resource.liveness import LeaseStanding, LeaseVerdict, assess_lease
from loommux.resource.model import (
    ClientLease,
    KernelResource,
    LeaseClient,
    ResourceAddress,
    ResourceLifecycle,
)
from loommux.resource.policy import LeasePolicy, LeasePolicyManager
from loommux.session import IPythonSession

SessionFactory = Callable[[], IPythonSession]
logger = logging.getLogger(__name__)


class ResourceManagerError(RuntimeError):
    """Base failure for the logical resource control plane."""


class ResourceProvisionError(ResourceManagerError):
    """A resource could not start its private IPython session."""


class ResourceNotFoundError(ResourceManagerError):
    """The requested resource is no longer registered."""


class ResourceBusyError(ResourceManagerError):
    """A non-forced control operation found active resource work."""


class KernelResourceManager:
    """Own many independently provisioned IPython workbenches.

    A provisioning task is registered per address before kernel startup begins.
    Concurrent callers for the same address await that task, while unrelated
    addresses remain free to provision in parallel.
    """

    def __init__(
        self,
        workspace: Path,
        workspace_resolution: str,
        *,
        session_factory: SessionFactory = IPythonSession,
        policy_manager: LeasePolicyManager | None = None,
        connections: ConnectionRegistry | None = None,
        sweep_interval_seconds: float = 10,
        orphan_grace_seconds: float = 30,
    ) -> None:
        for label, value in {
            "sweep_interval_seconds": sweep_interval_seconds,
            "orphan_grace_seconds": orphan_grace_seconds,
        }.items():
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{label} must be a positive finite number")
        self.workspace = workspace.resolve(strict=False)
        self.workspace_resolution = workspace_resolution
        self._session_factory = session_factory
        self.policy_manager = policy_manager or LeasePolicyManager()
        self.connections = connections or ConnectionRegistry()
        self._sweep_interval_seconds = sweep_interval_seconds
        self._orphan_grace_seconds = orphan_grace_seconds
        self._resources_by_key: dict[str, KernelResource] = {}
        self._resources_by_id: dict[str, KernelResource] = {}
        self._provisioning_by_key: dict[str, asyncio.Task[KernelResource]] = {}
        self._registry_lock = asyncio.Lock()
        self._stopping = False
        self._stop_event = asyncio.Event()
        self._sweeper_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._stopping:
            raise ResourceManagerError("resource manager is stopping")
        self._stop_event.clear()
        if self._sweeper_task is None:
            self._sweeper_task = asyncio.create_task(
                self._sweeper_loop(),
                name="loommux-resource-sweeper",
            )

    async def get_or_create(self, address: ResourceAddress) -> KernelResource:
        async with self._registry_lock:
            if self._stopping:
                raise ResourceManagerError("resource manager is stopping")
            resource = self._resources_by_key.get(address.key)
            if resource is not None:
                return resource

            provisioning = self._provisioning_by_key.get(address.key)
            if provisioning is None:
                provisioning = asyncio.create_task(
                    self._provision(address),
                    name=f"loommux-provision-{address.key}",
                )
                self._provisioning_by_key[address.key] = provisioning

        return await asyncio.shield(provisioning)

    async def get_by_id(self, resource_id: str) -> KernelResource | None:
        async with self._registry_lock:
            return self._resources_by_id.get(resource_id)

    @asynccontextmanager
    async def operation(
        self,
        address: ResourceAddress,
        client: LeaseClient,
        policy: LeasePolicy,
    ) -> AsyncIterator[KernelResource]:
        while True:
            resource = await self.get_or_create(address)
            await self._recover_if_needed(resource)
            async with self._registry_lock:
                # Recovery may suspend, and a resource retired meanwhile is no
                # longer registered. The retry cannot spin without yielding:
                # with no suspension nothing could have retired it, and a
                # retired address makes get_or_create return its successor or
                # await a new provisioning task.
                if self._resources_by_key.get(address.key) is not resource:
                    continue
                if resource.lifecycle is ResourceLifecycle.ORPHANED:
                    resource.lifecycle = ResourceLifecycle.RUNNING
                    resource.orphaned_at = None
                lease = resource.client_leases.get(client.client_id)
                if lease is None:
                    lease = ClientLease.create(client, policy)
                    resource.client_leases[client.client_id] = lease
                    self.connections.presence(client.client_id)
                resource.active_operations += 1
                lease.active_operations += 1
                self._record_request_locked(lease)
                break
        try:
            yield resource
        finally:
            async with self._registry_lock:
                resource.active_operations = max(
                    0,
                    resource.active_operations - 1,
                )
                lease = resource.client_leases.get(client.client_id)
                if lease is not None:
                    lease.active_operations = max(
                        0,
                        lease.active_operations - 1,
                    )
                    if resource.lifecycle is ResourceLifecycle.RUNNING:
                        self._renew_lease_locked(lease)

    async def renew_client_lease(
        self,
        address_key: str,
        client_id: str,
    ) -> bool:
        async with self._registry_lock:
            resource = self._resources_by_key.get(address_key)
            lease = None if resource is None else resource.client_leases.get(client_id)
            if lease is None:
                return False
            self._record_request_locked(lease)
            return True

    async def client_verdict(
        self,
        address_key: str,
        client_id: str,
    ) -> LeaseVerdict | None:
        """Judge one client's lease without renewing it."""
        async with self._registry_lock:
            resource = self._resources_by_key.get(address_key)
            lease = None if resource is None else resource.client_leases.get(client_id)
            if resource is None or lease is None:
                return None
            return self._assess(resource, lease, monotonic())

    async def list_resources(self) -> list[KernelResource]:
        async with self._registry_lock:
            return sorted(
                self._resources_by_id.values(),
                key=lambda resource: resource.created_at,
            )

    async def recycle(
        self,
        resource_id: str,
        *,
        force: bool = False,
        reason: str = "manual recycle",
    ) -> KernelResource:
        resource = await self._require_resource(resource_id)
        async with resource.lifecycle_lock:
            async with self._registry_lock:
                if self._resources_by_id.get(resource_id) is not resource:
                    raise ResourceNotFoundError("resource was not found")
                if resource.is_busy and not force:
                    raise ResourceBusyError("resource still has active work")
                self._detach_locked(resource)
                resource.lifecycle = ResourceLifecycle.CLOSING
                resource.closing_reason = reason

            await asyncio.to_thread(resource.session.close)
            resource.lifecycle = ResourceLifecycle.STOPPED
            return resource

    async def recycle_idle(self) -> int:
        async with self._registry_lock:
            resource_ids = [
                resource.resource_id
                for resource in self._resources_by_id.values()
                if not resource.is_busy
            ]
        results = await asyncio.gather(
            *(
                self.recycle(
                    resource_id,
                    reason="manual idle cleanup",
                )
                for resource_id in resource_ids
            ),
            return_exceptions=True,
        )
        return sum(isinstance(result, KernelResource) for result in results)

    async def recycle_all(self) -> int:
        async with self._registry_lock:
            resource_ids = list(self._resources_by_id)
        results = await asyncio.gather(
            *(
                self.recycle(
                    resource_id,
                    force=True,
                    reason="manual recycle all",
                )
                for resource_id in resource_ids
            ),
            return_exceptions=True,
        )
        return sum(isinstance(result, KernelResource) for result in results)

    async def interrupt(self, resource_id: str) -> dict[str, Any]:
        resource = await self._require_resource(resource_id)
        async with resource.lifecycle_lock:
            await self._assert_registered(resource)
            return await asyncio.to_thread(resource.session.interrupt)

    async def restart(self, resource_id: str) -> dict[str, Any]:
        resource = await self._require_resource(resource_id)
        async with resource.lifecycle_lock:
            await self._assert_registered(resource)
            result = await asyncio.to_thread(resource.session.restart)
            if not result.get("ok"):
                # The session is left without a kernel; the next operation on
                # this resource recovers it.
                raise ResourceProvisionError(
                    str(result.get("message", "kernel restart failed"))
                )
            return result

    async def health(self, resource_id: str) -> dict[str, Any]:
        """Observe kernel liveness without creating or renewing a client lease."""
        resource = await self._require_resource(resource_id)
        async with resource.lifecycle_lock:
            await self._assert_registered(resource)
            status = resource.session.status()
            healthy = status.get("kernel_started") is True
            return {
                "ok": healthy,
                "resource_id": resource.resource_id,
                "lifecycle": resource.lifecycle.value,
                "kernel_pid": status.get("kernel_pid"),
                "busy": resource.is_busy,
                "message": (
                    "kernel process is running"
                    if healthy
                    else "kernel process is not running"
                ),
            }

    async def stop(self) -> None:
        async with self._registry_lock:
            self._stopping = True
        self._stop_event.set()
        if self._sweeper_task is not None:
            await self._sweeper_task
            self._sweeper_task = None
        async with self._registry_lock:
            provisioning = list(self._provisioning_by_key.values())
            resources = list(self._resources_by_id.values())
            self._resources_by_key.clear()
            self._resources_by_id.clear()
            for resource in resources:
                resource.lifecycle = ResourceLifecycle.CLOSING
                resource.closing_reason = "server shutdown"

        if provisioning:
            await asyncio.gather(*provisioning, return_exceptions=True)
            async with self._registry_lock:
                late_resources = list(self._resources_by_id.values())
                self._resources_by_key.clear()
                self._resources_by_id.clear()
                for resource in late_resources:
                    resource.lifecycle = ResourceLifecycle.CLOSING
                    resource.closing_reason = "server shutdown"
            resources.extend(late_resources)

        await asyncio.gather(
            *(self._close_detached(resource) for resource in resources),
            return_exceptions=True,
        )

    async def sweep_once(self) -> int:
        now = monotonic()
        async with self._registry_lock:
            for resource in self._resources_by_id.values():
                for client_id, lease in list(resource.client_leases.items()):
                    verdict = self._assess(resource, lease, now)
                    self._report_alert_locked(resource, lease, verdict)
                    if lease.active_operations == 0 and verdict.expired(now):
                        del resource.client_leases[client_id]
            self.connections.prune(
                keep={
                    client_id
                    for resource in self._resources_by_id.values()
                    for client_id in resource.client_leases
                }
            )

            closing: list[KernelResource] = []
            for resource in list(self._resources_by_id.values()):
                if resource.client_leases:
                    continue
                if resource.orphaned_at is None:
                    resource.orphaned_at = now
                    resource.lifecycle = ResourceLifecycle.ORPHANED
                idle = (
                    resource.active_operations == 0
                    and resource.current_execution is None
                )
                grace_expired = now - resource.orphaned_at >= self._orphan_grace_seconds
                if idle or grace_expired:
                    self._detach_locked(resource)
                    resource.lifecycle = ResourceLifecycle.CLOSING
                    resource.closing_reason = (
                        "lease expired"
                        if idle
                        else "orphan grace expired"
                    )
                    closing.append(resource)

        await asyncio.gather(
            *(self._close_detached(resource) for resource in closing),
            return_exceptions=True,
        )
        return len(closing)

    async def _recover_if_needed(self, resource: KernelResource) -> None:
        if resource.session.status().get("kernel_started", False):
            return
        async with resource.lifecycle_lock:
            if resource.session.status().get("kernel_started", False):
                return
            async with self._registry_lock:
                if self._resources_by_id.get(resource.resource_id) is not resource:
                    raise ResourceNotFoundError("resource was retired during recovery")
            result = await asyncio.to_thread(resource.session.restart)
            if not result.get("ok"):
                async with self._registry_lock:
                    self._detach_locked(resource)
                    resource.lifecycle = ResourceLifecycle.CLOSING
                    resource.closing_reason = "kernel recovery failed"
                await asyncio.to_thread(resource.session.close)
                resource.lifecycle = ResourceLifecycle.STOPPED
                raise ResourceProvisionError(
                    str(result.get("message", "kernel recovery failed"))
                )

    async def _require_resource(self, resource_id: str) -> KernelResource:
        async with self._registry_lock:
            resource = self._resources_by_id.get(resource_id)
            if resource is None:
                raise ResourceNotFoundError("resource was not found")
            return resource

    async def _assert_registered(self, resource: KernelResource) -> None:
        async with self._registry_lock:
            if self._resources_by_id.get(resource.resource_id) is not resource:
                raise ResourceNotFoundError("resource was not found")

    @staticmethod
    async def _close_detached(resource: KernelResource) -> None:
        async with resource.lifecycle_lock:
            await asyncio.to_thread(resource.session.close)
            resource.lifecycle = ResourceLifecycle.STOPPED

    async def snapshot(self) -> list[dict[str, Any]]:
        resources = await self.list_resources()
        return [self._snapshot_resource(resource) for resource in resources]

    async def _provision(self, address: ResourceAddress) -> KernelResource:
        session = self._session_factory()
        try:
            startup = await asyncio.to_thread(
                session.start_workspace,
                self.workspace,
                self.workspace_resolution,
            )
            if not startup.get("ok"):
                raise ResourceProvisionError(
                    str(startup.get("message", "IPython session failed to start"))
                )
            resource = KernelResource.create(address, session)
            async with self._registry_lock:
                if self._stopping:
                    raise ResourceManagerError("resource manager is stopping")
                self._resources_by_key[address.key] = resource
                self._resources_by_id[resource.resource_id] = resource
            return resource
        except BaseException:
            await asyncio.to_thread(session.close)
            raise
        finally:
            async with self._registry_lock:
                current = self._provisioning_by_key.get(address.key)
                if current is asyncio.current_task():
                    self._provisioning_by_key.pop(address.key, None)

    def _detach_locked(self, resource: KernelResource) -> None:
        self._resources_by_id.pop(resource.resource_id, None)
        if self._resources_by_key.get(resource.address.key) is resource:
            self._resources_by_key.pop(resource.address.key, None)

    @staticmethod
    def _renew_lease_locked(lease: ClientLease) -> None:
        lease.last_seen_at = datetime.now(UTC)
        lease.renewed_at = monotonic()

    @staticmethod
    def _record_request_locked(lease: ClientLease) -> None:
        lease.last_seen_at = datetime.now(UTC)
        lease.renewed_at = lease.requested_at = monotonic()

    @staticmethod
    def _report_alert_locked(
        resource: KernelResource,
        lease: ClientLease,
        verdict: LeaseVerdict,
    ) -> None:
        alert: LeaseStanding | None = verdict.standing if verdict.standing.alert else None
        if alert is lease.reported_alert:
            return
        lease.reported_alert = alert
        if alert is not None:
            logger.warning(
                "lease alert: %s on %s is %s; the client does not keep open the standalone GET stream this connection lease relies on, so it falls back to the %g-second activity window",
                lease.client.display_name,
                resource.address.display_name,
                alert.value,
                verdict.window_seconds,
            )

    def _assess(
        self,
        resource: KernelResource,
        lease: ClientLease,
        now: float,
    ) -> LeaseVerdict:
        return assess_lease(
            lease,
            self.connections.presence(lease.client.client_id),
            shared=resource.address.shared,
            now=now,
        )

    async def _sweeper_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._sweep_interval_seconds,
                )
            except TimeoutError:
                try:
                    await self.sweep_once()
                except Exception:
                    # One observation failure must not permanently disable lease
                    # reclamation for every other resource in this server.
                    logger.exception("kernel resource sweep failed")

    def _snapshot_resource(self, resource: KernelResource) -> dict[str, Any]:
        session_status = resource.session.status()
        now = monotonic()
        leases = []
        for lease in sorted(
            resource.client_leases.values(),
            key=lambda item: item.created_at,
        ):
            verdict = self._assess(resource, lease, now)
            leases.append(
                {
                    "lease_id": lease.lease_id,
                    "client_id": lease.client.client_id,
                    "client_name": lease.client.display_name,
                    "mode": lease.policy.mode.value,
                    "policy_generation": lease.policy.generation,
                    "created_at": lease.created_at.isoformat(),
                    "last_seen_at": lease.last_seen_at.isoformat(),
                    "active_operations": lease.active_operations,
                    "standing": verdict.standing.value,
                    "alert": verdict.standing.alert,
                    "timeout_seconds": verdict.window_seconds,
                    "remaining_seconds": verdict.remaining_seconds(now),
                    "grace_remaining_seconds": verdict.grace_remaining_seconds(now),
                }
            )
        return {
            "resource_id": resource.resource_id,
            "key": resource.address.key,
            "name": resource.address.display_name,
            "scope": resource.address.scope,
            "shared": resource.address.shared,
            "lifecycle": resource.lifecycle.value,
            "created_at": resource.created_at.isoformat(),
            "active_operations": resource.active_operations,
            "busy": resource.is_busy,
            "kernel_pid": session_status.get("kernel_pid"),
            "current_execution": session_status.get("current_execution"),
            "recent_execution": session_status.get("recent_execution"),
            "execution_count": len(resource.session.executions),
            "lease_count": len(leases),
            "leases": leases,
        }
