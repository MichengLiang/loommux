"""Provision, register, observe, and retire logical IPython resources."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loommux.resource.model import KernelResource, ResourceAddress, ResourceLifecycle
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
    ) -> None:
        self.workspace = workspace.resolve(strict=False)
        self.workspace_resolution = workspace_resolution
        self._session_factory = session_factory
        self._resources_by_key: dict[str, KernelResource] = {}
        self._resources_by_id: dict[str, KernelResource] = {}
        self._provisioning_by_key: dict[str, asyncio.Task[KernelResource]] = {}
        self._registry_lock = asyncio.Lock()
        self._stopping = False

    async def get_or_create(self, address: ResourceAddress) -> KernelResource:
        async with self._registry_lock:
            if self._stopping:
                raise ResourceManagerError("resource manager is stopping")
            resource = self._resources_by_key.get(address.key)
            if resource is not None and resource.lifecycle in {
                ResourceLifecycle.RUNNING,
                ResourceLifecycle.ORPHANED,
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
    def _snapshot_resource(resource: KernelResource) -> dict[str, Any]:
        session_status = resource.session.status()
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
            "lease_count": len(resource.client_leases),
        }
