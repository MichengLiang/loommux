"""Describe logical IPython resources and their participating clients."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from time import monotonic
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from loommux.resource.liveness import LeaseStanding
    from loommux.resource.policy import LeasePolicy
    from loommux.session import IPythonSession


class ResourceLifecycle(StrEnum):
    """Stable lifecycle states for one logical IPython resource."""

    RUNNING = "running"
    ORPHANED = "orphaned"
    CLOSING = "closing"
    STOPPED = "stopped"
    CRASHED = "crashed"


@dataclass(frozen=True)
class ResourceAddress:
    """A transport-neutral address for a private or named shared resource."""

    key: str
    display_name: str
    scope: str
    shared: bool


@dataclass(frozen=True)
class LeaseClient:
    """One protocol participant holding an independent resource lease."""

    client_id: str
    display_name: str


@dataclass
class ClientLease:
    """One client's immutable policy snapshot and mutable liveness facts.

    ``renewed_at`` and ``requested_at`` are monotonic timestamps.
    ``renewed_at`` moves whenever the lease is renewed, including when the
    server finishes an operation; ``requested_at`` moves only when the client
    itself sends something (a tool call or a ping), so it alone is evidence
    that the client is still alive. ``last_seen_at`` is the latest renewal on
    the wall clock, kept only for operators reading snapshots.
    ``reported_alert`` is the alert standing the server last logged for this
    lease, so a sweep logs each entry into an alert once rather than every
    pass; it is ``None`` while the lease is not alerting.
    """

    lease_id: str
    client: LeaseClient
    policy: LeasePolicy
    created_at: datetime
    last_seen_at: datetime
    renewed_at: float
    requested_at: float
    active_operations: int = 0
    reported_alert: LeaseStanding | None = None

    @classmethod
    def create(
        cls,
        client: LeaseClient,
        policy: LeasePolicy,
        *,
        now: datetime | None = None,
    ) -> ClientLease:
        timestamp = datetime.now(UTC) if now is None else now
        instant = monotonic()
        return cls(
            lease_id=uuid4().hex,
            client=client,
            policy=policy,
            created_at=timestamp,
            last_seen_at=timestamp,
            renewed_at=instant,
            requested_at=instant,
        )


@dataclass
class KernelResource:
    """A stable logical workbench whose kernel process may be replaced."""

    resource_id: str
    address: ResourceAddress
    session: IPythonSession
    created_at: datetime
    lifecycle: ResourceLifecycle = ResourceLifecycle.RUNNING
    client_leases: dict[str, ClientLease] = field(default_factory=dict)
    active_operations: int = 0
    orphaned_at: float | None = None
    closing_reason: str | None = None
    lifecycle_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @classmethod
    def create(
        cls,
        address: ResourceAddress,
        session: IPythonSession,
        *,
        now: datetime | None = None,
    ) -> KernelResource:
        return cls(
            resource_id=uuid4().hex,
            address=address,
            session=session,
            created_at=datetime.now(UTC) if now is None else now,
        )

    @property
    def kernel_pid(self) -> int | None:
        kernel = self.session.kernel
        return kernel.pid if kernel is not None else None

    @property
    def current_execution(self) -> int | None:
        return self.session.current_execution

    @property
    def is_busy(self) -> bool:
        return self.active_operations > 0 or self.current_execution is not None
