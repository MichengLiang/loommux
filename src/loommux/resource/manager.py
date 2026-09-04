"""Provision, register, observe, and retire logical IPython resources."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

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
        sweep_interval_seconds: float = 10,
        orphan_grace_seconds: float = 30,
    ) -> None:
        self.workspace = workspace.resolve(strict=False)
        self.workspace_resolution = workspace_resolution
        self._session_factory = session_factory
        self.policy_manager = policy_manager or LeasePolicyManager()
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
            if resource is not None and resource.lifecycle in {
                ResourceLifecycle.RUNNING,
                ResourceLifecycle.ORPHANED,
                ResourceLifecycle.CRASHED,
            }:
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
                if self._resources_by_key.get(address.key) is not resource:
                    continue
                if resource.lifecycle is ResourceLifecycle.ORPHANED:
                    resource.lifecycle = ResourceLifecycle.RUNNING
                    resource.orphaned_at = None
                if resource.lifecycle is not ResourceLifecycle.RUNNING:
                    continue
                lease = resource.client_leases.get(client.client_id)
                if lease is None:
                    lease = ClientLease.create(client, policy)
                    resource.client_leases[client.client_id] = lease
                resource.active_operations += 1
                lease.active_operations += 1
                self._touch_lease_locked(resource, lease)
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
                        self._touch_lease_locked(resource, lease)

    async def renew_client_lease(
        self,
        address_key: str,
        client_id: str,
    ) -> bool:
        async with self._registry_lock:
            resource = self._resources_by_key.get(address_key)
            if resource is None or resource.lifecycle is not ResourceLifecycle.RUNNING:
                return False
            lease = resource.client_leases.get(client_id)
            if lease is None:
                return False
            self._touch_lease_locked(resource, lease)
            return True

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
        async with self._registry_lock:
            resource = self._resources_by_id.get(resource_id)
            if resource is None:
                raise ResourceNotFoundError("resource was not found")
            if resource.is_busy and not force:
                raise ResourceBusyError("resource still has active work")
            self._detach_locked(resource)
            resource.lifecycle = ResourceLifecycle.CLOSING
            resource.closing_reason = reason

        await asyncio.to_thread(resource.session.close)
        resource.lifecycle = ResourceLifecycle.STOPPED
        return resource

    async def stop(self) -> None:
        self._stop_event.set()
        if self._sweeper_task is not None:
            await self._sweeper_task
            self._sweeper_task = None
        async with self._registry_lock:
            self._stopping = True
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
            *(asyncio.to_thread(resource.session.close) for resource in resources),
            return_exceptions=True,
        )
        for resource in resources:
            resource.lifecycle = ResourceLifecycle.STOPPED

    async def sweep_once(self) -> int:
        now = monotonic()
        async with self._registry_lock:
            for resource in self._resources_by_id.values():
                if (
                    resource.lifecycle is ResourceLifecycle.RUNNING
                    and not resource.session.status().get("kernel_started", False)
                ):
                    resource.lifecycle = ResourceLifecycle.CRASHED
                expired_clients = [
                    client_id
                    for client_id, lease in resource.client_leases.items()
                    if lease.active_operations == 0 and lease.deadline <= now
                ]
                for client_id in expired_clients:
                    resource.client_leases.pop(client_id)

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
            *(asyncio.to_thread(resource.session.close) for resource in closing),
            return_exceptions=True,
        )
        for resource in closing:
            resource.lifecycle = ResourceLifecycle.STOPPED
        return len(closing)

    async def _recover_if_needed(self, resource: KernelResource) -> None:
        if resource.session.status().get("kernel_started", False):
            return
        async with resource.recovery_lock:
            if resource.session.status().get("kernel_started", False):
                return
            async with self._registry_lock:
                if self._resources_by_id.get(resource.resource_id) is not resource:
                    raise ResourceNotFoundError("resource was retired during recovery")
                resource.lifecycle = ResourceLifecycle.CRASHED
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
            resource.lifecycle = ResourceLifecycle.RUNNING

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
    def _touch_lease_locked(
        resource: KernelResource,
        lease: ClientLease,
    ) -> None:
        lease.last_seen_at = datetime.now(UTC)
        lease.deadline = monotonic() + lease.policy.timeout_for(
            shared=resource.address.shared,
        )

    async def _sweeper_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._sweep_interval_seconds,
                )
            except TimeoutError:
                await self.sweep_once()

    @staticmethod
    def _snapshot_resource(resource: KernelResource) -> dict[str, Any]:
        session_status = resource.session.status()
        now = monotonic()
        leases = [
            {
                "lease_id": lease.lease_id,
                "client_id": lease.client.client_id,
                "client_name": lease.client.display_name,
                "mode": lease.policy.mode.value,
                "policy_generation": lease.policy.generation,
                "created_at": lease.created_at.isoformat(),
                "last_seen_at": lease.last_seen_at.isoformat(),
                "active_operations": lease.active_operations,
                "timeout_seconds": lease.policy.timeout_for(
                    shared=resource.address.shared,
                ),
                "remaining_seconds": max(0, round(lease.deadline - now, 1)),
            }
            for lease in sorted(
                resource.client_leases.values(),
                key=lambda item: item.created_at,
            )
        ]
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
