"""Provision, register, observe, and retire logical IPython resources."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, nullcontext
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

from loommux.identity import KernelIdentity
from loommux.resource.connections import ConnectionRegistry
from loommux.resource.liveness import LeaseStanding, LeaseVerdict, assess_lease
from loommux.resource.model import (
    ClientLease,
    KernelRequest,
    KernelResource,
    LeaseClient,
    ResourceLifecycle,
    resource_key,
)
from loommux.resource.policy import LeasePolicy, LeasePolicyManager
from loommux.resource.routing import private_kernel_name, validate_kernel_name
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

    A provisioning task is registered per kernel key before kernel startup
    begins. Concurrent callers for the same key await that task, while
    unrelated kernels remain free to provision in parallel.
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
        control_url: str | None = None,
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
        self._control_url = control_url
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

    async def get_or_create(self, name: str, *, home: str | None = None, named: bool = True) -> KernelResource:
        """Return the kernel called ``name`` in ``home``, provisioning it if absent.

        ``home`` is ``None`` for a main kernel. A child kernel carries its home
        from the moment it is registered, so no sweep can find it unanchored
        while its main kernel is anchored.
        """
        async with self._registry_lock:
            if self._stopping:
                raise ResourceManagerError("resource manager is stopping")
            claim = self._claim_locked(name, home, named)
        return await _settled(claim)

    def _claim_locked(self, name: str, home: str | None, named: bool) -> KernelResource | asyncio.Task[KernelResource]:
        """The registered kernel with this name in this home, or the task starting it."""
        key = resource_key(name, home)
        resource = self._resources_by_key.get(key)
        if resource is not None:
            return resource
        provisioning = self._provisioning_by_key.get(key)
        if provisioning is None:
            provisioning = asyncio.create_task(
                self._provision(name, home, named),
                name=f"loommux-provision-{key}",
            )
            self._provisioning_by_key[key] = provisioning
        return provisioning

    def _free_main_name_locked(self, name: str) -> str:
        """``name``, or ``name-2``, ``name-3``... whichever no main kernel has.

        Kernels still starting count, so two sessions asking for one name at
        the same moment receive two names rather than one shared kernel.
        """
        candidate, number = name, 1
        while (key := resource_key(candidate, None)) in self._resources_by_key or key in self._provisioning_by_key:
            number += 1
            candidate = f"{name}-{number}"
        return candidate

    def _bound_locked(self, client_id: str) -> KernelResource | None:
        # The lease is the binding. Child kernels hold no client leases, so at
        # most one main kernel holds this session's; a second record of the
        # same fact could disagree with the leases the sweep releases.
        return next((resource for resource in self._resources_by_id.values() if client_id in resource.client_leases), None)

    async def _session_kernel(self, client: LeaseClient, request: KernelRequest | None) -> KernelResource:
        """The main kernel this call's session works in, chosen on its first call.

        A session holding no lease is on its first call: either it never
        called, or the sweep released its lease and with it the binding.
        """
        async with self._registry_lock:
            if self._stopping:
                raise ResourceManagerError("resource manager is stopping")
            bound = self._bound_locked(client.client_id)
            if bound is not None:
                return bound
            if request is not None:
                claim = self._claim_locked(request.name, None, True)
            else:
                name = private_kernel_name(client)
                # A registered kernel under this name is another session's,
                # whose id shares the prefix; a provisioning task under it is
                # this session's own concurrent first call, which it joins.
                if resource_key(name, None) in self._resources_by_key:
                    name = self._free_main_name_locked(name)
                claim = self._claim_locked(name, None, False)
        return await _settled(claim)

    async def get_by_id(self, resource_id: str) -> KernelResource | None:
        async with self._registry_lock:
            return self._resources_by_id.get(resource_id)

    async def ensure_held(self, name: str, holder_id: str | None) -> KernelResource:
        """Return the kernel that ``holder_id``'s kernel names, opening a child kernel.

        Inside a kernel, names resolve in its home: the home's main kernel and
        the asking kernel by their own name or id, otherwise a child kernel of
        the home, opened on first use. A child kernel asking for a kernel opens
        it in the same home, so homes have one level.

        Without a holder the caller is not a kernel and nothing could anchor a
        new kernel, so only a main kernel by name, or any kernel by id,
        resolves.
        """
        name = validate_kernel_name(name)
        async with self._registry_lock:
            if holder_id is None:
                existing = self._resources_by_id.get(name) or self._resources_by_key.get(resource_key(name, None))
                if existing is None:
                    raise ResourceNotFoundError(f"no main kernel named {name!r} and no kernel with that id; only a kernel can open one")
                return existing
            holder = self._resources_by_id.get(holder_id)
            if holder is None:
                raise ResourceNotFoundError("holder resource was not found")
            home_id = holder.home or holder.resource_id
            for candidate in (holder, self._resources_by_id.get(home_id)):
                if candidate is not None and name in (candidate.resource_id, candidate.name):
                    return candidate
            existing = self._resources_by_key.get(resource_key(name, home_id)) or next(
                (resource for resource in self._resources_by_id.values() if resource.home == home_id and resource.resource_id == name),
                None,
            )
            if existing is not None:
                return existing
            if self._stopping:
                raise ResourceManagerError("resource manager is stopping")
            claim = self._claim_locked(name, home_id, True)
        resource = await _settled(claim)
        async with self._registry_lock:
            if home_id not in self._resources_by_id:
                # The home retired while the kernel started; nothing anchors it.
                raise ResourceNotFoundError("holder resource was not found")
        return resource

    @asynccontextmanager
    async def program_operation(self, resource_id: str) -> AsyncIterator[KernelResource]:
        """Admit one control-plane operation that runs or observes cells.

        Unlike an MCP operation it creates and renews no lease: the program
        calling it is a kernel, and what keeps the target alive is its
        holding, not this request.
        """
        resource = await self._require_resource(resource_id)
        await self._recover_if_needed(resource)
        async with self._registry_lock:
            if self._resources_by_id.get(resource_id) is not resource:
                raise ResourceNotFoundError("resource was not found")
            resource.active_operations += 1
        try:
            yield resource
        finally:
            async with self._registry_lock:
                resource.active_operations = max(0, resource.active_operations - 1)

    @asynccontextmanager
    async def operation(
        self,
        client: LeaseClient,
        policy: LeasePolicy,
        *,
        request: KernelRequest | None = None,
        replaces_kernel: bool = False,
    ) -> AsyncIterator[KernelResource]:
        """Admit one client operation on the main kernel its session works in.

        ``request`` names the main kernel to choose on the session's first
        call; once the session holds a lease it works where that lease is.

        ``replaces_kernel`` marks an operation that may swap the session's
        kernel process, such as a restart. It then holds the resource's
        lifecycle lock, which already serializes recovery, control-plane
        restarts, and retirement: a concurrent call that reads the replacement
        gap waits in recovery instead of starting a second kernel. Of two
        concurrent replacements, the kernel installed first is overwritten by
        the other and never shut down.
        """
        while True:
            resource = await self._session_kernel(client, request)
            await self._recover_if_needed(resource)
            async with self._registry_lock:
                # Recovery may suspend, and a resource retired meanwhile is no
                # longer registered. The retry cannot spin without yielding:
                # with no suspension nothing could have retired it, and a
                # retired kernel makes the next choice return the session's
                # successor or await a new provisioning task. A concurrent
                # first call of the same session may also have bound it to
                # another kernel meanwhile; the next choice returns that one.
                if self._resources_by_id.get(resource.resource_id) is not resource:
                    continue
                bound = self._bound_locked(client.client_id)
                if bound is not None and bound is not resource:
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
            async with resource.lifecycle_lock if replaces_kernel else nullcontext():
                if replaces_kernel:
                    await self._assert_registered(resource)
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
        client_id: str,
    ) -> bool:
        async with self._registry_lock:
            resource = self._bound_locked(client_id)
            if resource is None:
                return False
            self._record_request_locked(resource.client_leases[client_id])
            return True

    async def client_verdict(
        self,
        client_id: str,
    ) -> LeaseVerdict | None:
        """Judge one client's lease without renewing it."""
        async with self._registry_lock:
            resource = self._bound_locked(client_id)
            if resource is None:
                return None
            return self._assess(resource, resource.client_leases[client_id], monotonic())

    async def list_resources(self) -> list[KernelResource]:
        async with self._registry_lock:
            return sorted(
                self._resources_by_id.values(),
                key=lambda resource: resource.created_at,
            )

    async def list_home(self, resource_id: str) -> list[KernelResource]:
        """The kernels of ``resource_id``'s home: its main kernel and child kernels, oldest first."""
        async with self._registry_lock:
            member = self._resources_by_id.get(resource_id)
            if member is None:
                raise ResourceNotFoundError("resource was not found")
            home_id = member.home or member.resource_id
            return sorted(
                (resource for resource in self._resources_by_id.values() if home_id in (resource.resource_id, resource.home)),
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

            anchored = self._anchored_locked()
            closing: list[KernelResource] = []
            for resource in list(self._resources_by_id.values()):
                if resource.resource_id in anchored:
                    if resource.lifecycle is ResourceLifecycle.ORPHANED:
                        # A session's lease anchored it again during the grace.
                        resource.lifecycle = ResourceLifecycle.RUNNING
                        resource.orphaned_at = None
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
                    # Retired while this call waited for the lock; operation()
                    # sees it unregistered and retries with the successor.
                    return
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

    async def snapshot(self, *, home_of: str | None = None) -> list[dict[str, Any]]:
        """Every kernel, or with ``home_of`` only the kernels of that kernel's home."""
        resources = await (self.list_resources() if home_of is None else self.list_home(home_of))
        return [self._snapshot_resource(resource) for resource in resources]

    async def snapshot_one(self, resource_id: str) -> dict[str, Any]:
        return self._snapshot_resource(await self._require_resource(resource_id))

    async def _provision(self, name: str, home: str | None, named: bool) -> KernelResource:
        session = self._session_factory()
        # The id exists before the kernel does, so the kernel can be told who
        # it is when it starts.
        resource_id = uuid4().hex
        key = resource_key(name, home)
        try:
            startup = await asyncio.to_thread(
                session.start_workspace,
                self.workspace,
                self.workspace_resolution,
                identity=KernelIdentity(resource_id, name, self._control_url),
            )
            if not startup.get("ok"):
                raise ResourceProvisionError(
                    str(startup.get("message", "IPython session failed to start"))
                )
            resource = KernelResource.create(name, session, home=home, named=named, resource_id=resource_id)
            async with self._registry_lock:
                if self._stopping:
                    raise ResourceManagerError("resource manager is stopping")
                self._resources_by_key[key] = resource
                self._resources_by_id[resource.resource_id] = resource
            return resource
        except BaseException:
            await asyncio.to_thread(session.close)
            raise
        finally:
            async with self._registry_lock:
                current = self._provisioning_by_key.get(key)
                if current is asyncio.current_task():
                    self._provisioning_by_key.pop(key, None)

    def _detach_locked(self, resource: KernelResource) -> None:
        self._resources_by_id.pop(resource.resource_id, None)
        if self._resources_by_key.get(resource.key) is resource:
            self._resources_by_key.pop(resource.key, None)

    def _anchored_locked(self) -> set[str]:
        """Ids of main kernels a client lease holds, and of their child kernels.

        A child kernel of a retired main kernel keeps its home id, which no
        longer anchors anything, so it is released with its home.
        """
        leased = {resource.resource_id for resource in self._resources_by_id.values() if resource.client_leases}
        return leased | {resource.resource_id for resource in self._resources_by_id.values() if resource.home in leased}

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
                resource.name,
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
            named=resource.named,
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
            "name": resource.name,
            "scope": "main" if resource.home is None else "child",
            "home": None if resource.home is None else self._home_name(resource.home),
            "named": resource.named,
            "lifecycle": resource.lifecycle.value,
            "created_at": resource.created_at.isoformat(),
            "active_operations": resource.active_operations,
            "busy": resource.is_busy,
            "kernel_pid": session_status.get("kernel_pid"),
            "kernel_state": (
                "running"
                if session_status.get("kernel_started")
                else "restarting"
                if session_status.get("kernel_restarting")
                else "stopped"
            ),
            "current_execution": session_status.get("current_execution"),
            "recent_execution": session_status.get("recent_execution"),
            "execution_count": len(resource.session.executions),
            "lease_count": len(leases),
            "leases": leases,
        }

    def _home_name(self, home_id: str) -> str | None:
        home = self._resources_by_id.get(home_id)
        return None if home is None else home.name


async def _settled(claim: KernelResource | asyncio.Task[KernelResource]) -> KernelResource:
    # Shielded so that a caller cancelled while waiting does not cancel a
    # start other callers of the same kernel are waiting for too.
    return claim if isinstance(claim, KernelResource) else await asyncio.shield(claim)
