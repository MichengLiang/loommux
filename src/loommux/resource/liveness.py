"""Judge whether one client lease still holds its resource."""

from __future__ import annotations

from dataclasses import dataclass

from loommux.resource.model import ClientLease


@dataclass(frozen=True)
class LeaseVerdict:
    """When a lease stops holding its resource, and the window that decides it."""

    expires_at: float
    window_seconds: float

    def remaining_seconds(self, now: float) -> float:
        return max(0, round(self.expires_at - now, 1))


def assess_lease(lease: ClientLease, *, shared: bool) -> LeaseVerdict:
    """Derive expiry from the lease's renewal facts and its pinned policy.

    Expiry is computed on demand rather than stored, so the sweeper, the
    snapshot, and any later liveness rule all read one decision.
    """
    window = lease.policy.timeout_for(shared=shared)
    return LeaseVerdict(expires_at=lease.renewed_at + window, window_seconds=window)
