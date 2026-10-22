"""Judge whether one client lease still holds its resource.

Activity and heartbeat leases are renewable: each renewal reopens a fixed
window. A connection lease is held for as long as its session's standalone
stream is open. When that signal is missing or contradicted, the lease
degrades to the activity window and raises an alert, because the client's
transport no longer behaves the way the mode relies on:

``unbound``
    The session never opened a stream within the grace period, yet it holds
    a lease, so it is calling tools without one.
``stream_lost``
    The stream closed, no replacement arrived within the grace period, and
    the client sent a request after the close, so it is still alive.

A stream that closes with no later request is an ordinary departure: the
lease is released when the grace period ends.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from loommux.resource.connections import SessionPresence
from loommux.resource.model import ClientLease
from loommux.resource.policy import LeaseMode


class LeaseStanding(StrEnum):
    """Why a lease currently holds, or no longer holds, its resource."""

    RENEWABLE = "renewable"
    CONNECTED = "connected"
    AWAITING_STREAM = "awaiting_stream"
    RECONNECTING = "reconnecting"
    DISCONNECTED = "disconnected"
    UNBOUND = "unbound"
    STREAM_LOST = "stream_lost"

    @property
    def alert(self) -> bool:
        return self in {LeaseStanding.UNBOUND, LeaseStanding.STREAM_LOST}


@dataclass(frozen=True)
class LeaseVerdict:
    """One lease's standing and, when scheduled, the instant it is released.

    ``expires_at`` is ``None`` while an open stream holds the lease or while
    the grace period is still deciding; ``grace_ends_at`` marks that
    decision. ``window_seconds`` is the duration that governs the current
    standing: the renewal window for renewable and alerting leases, the
    grace while awaiting, reconnecting, or after a stream has departed, and
    ``None`` while connected.
    """

    standing: LeaseStanding
    expires_at: float | None
    window_seconds: float | None
    grace_ends_at: float | None = None

    def expired(self, now: float) -> bool:
        return self.expires_at is not None and self.expires_at <= now

    def remaining_seconds(self, now: float) -> float | None:
        return None if self.expires_at is None else _remaining(self.expires_at, now)

    def grace_remaining_seconds(self, now: float) -> float | None:
        return None if self.grace_ends_at is None else _remaining(self.grace_ends_at, now)


def assess_lease(
    lease: ClientLease,
    presence: SessionPresence,
    *,
    named: bool,
    now: float,
) -> LeaseVerdict:
    """Derive the lease's standing from its renewal facts and its session.

    Expiry is computed on demand rather than stored, so the sweeper, the
    snapshot, and the tool-result alert all read one decision.
    """
    policy = lease.policy
    window = policy.timeout_for(named=named)
    if policy.mode is not LeaseMode.CONNECTION:
        return _renewal(LeaseStanding.RENEWABLE, lease, window)
    if presence.connected:
        return LeaseVerdict(LeaseStanding.CONNECTED, None, None)

    grace = policy.connection_grace_seconds
    if presence.closed_at is None:
        grace_ends_at = presence.first_seen_at + grace
        if now < grace_ends_at:
            return LeaseVerdict(LeaseStanding.AWAITING_STREAM, None, grace, grace_ends_at)
        return _renewal(LeaseStanding.UNBOUND, lease, window)

    grace_ends_at = presence.closed_at + grace
    if now < grace_ends_at:
        return LeaseVerdict(LeaseStanding.RECONNECTING, None, grace, grace_ends_at)
    if lease.requested_at > presence.closed_at:
        return _renewal(LeaseStanding.STREAM_LOST, lease, window)
    return LeaseVerdict(LeaseStanding.DISCONNECTED, grace_ends_at, grace)


def _renewal(standing: LeaseStanding, lease: ClientLease, window: float) -> LeaseVerdict:
    return LeaseVerdict(standing, lease.renewed_at + window, window)


def _remaining(instant: float, now: float) -> float:
    return max(0, round(instant - now, 1))
