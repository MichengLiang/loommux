"""Describe logical IPython resources and their participating clients.

A resource is a main kernel, which MCP sessions work in, or a child kernel,
which a kernel opened by name; ``docs/kernel-binding-design.md`` owns the
rules that relate them.
"""

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
    """What the resource manager has done with one logical IPython resource.

    A registered resource is ``running`` or ``orphaned``; ``closing`` and
    ``stopped`` follow its removal from the registry. Only the manager's own
    operations move a resource between states. Whether the current kernel
    process is alive is a separate fact that ``IPythonSession.status()``
    reports, because a restart leaves the session briefly without a kernel and
    a reading taken in that gap must not outlive it.
    """

    RUNNING = "running"
    ORPHANED = "orphaned"
    CLOSING = "closing"
    STOPPED = "stopped"


class RequestMode(StrEnum):
    """How a call asks for the main kernel its session works in."""

    # A connection header: every session configured with the name shares one
    # main kernel, opened by whichever arrives first.
    JOIN = "join"


@dataclass(frozen=True)
class KernelRequest:
    """The main kernel a call names, consulted while its session has none."""

    name: str
    mode: RequestMode


def resource_key(name: str, home: str | None) -> str:
    """The registry key of a kernel: main kernel names are unique among main
    kernels, child kernel names only inside their home."""
    return f"main:{name}" if home is None else f"child:{home}:{name}"


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
    """A stable logical workbench whose kernel process may be replaced.

    ``home`` is ``None`` for a main kernel and the main kernel's resource id
    for a child kernel; it never changes, so a child kernel stays one while
    its main kernel is being retired. A main kernel holds the client leases of
    the sessions working in it, and a child kernel holds none: it is anchored
    exactly when its main kernel is.

    ``named`` is false only for a main kernel whose name was generated for a
    session that asked for none, until it is renamed. Activity leases on such
    a kernel use the private window.
    """

    resource_id: str
    name: str
    session: IPythonSession
    created_at: datetime
    home: str | None = None
    named: bool = True
    lifecycle: ResourceLifecycle = ResourceLifecycle.RUNNING
    client_leases: dict[str, ClientLease] = field(default_factory=dict)
    active_operations: int = 0
    orphaned_at: float | None = None
    closing_reason: str | None = None
    lifecycle_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @classmethod
    def create(
        cls,
        name: str,
        session: IPythonSession,
        *,
        home: str | None = None,
        named: bool = True,
        resource_id: str | None = None,
        now: datetime | None = None,
    ) -> KernelResource:
        return cls(
            resource_id=uuid4().hex if resource_id is None else resource_id,
            name=name,
            session=session,
            created_at=datetime.now(UTC) if now is None else now,
            home=home,
            named=named,
        )

    @property
    def key(self) -> str:
        return resource_key(self.name, self.home)

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
