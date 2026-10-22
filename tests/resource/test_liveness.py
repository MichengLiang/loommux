from datetime import UTC, datetime

import pytest

from loommux.resource import LeaseClient, LeaseMode, LeasePolicy
from loommux.resource.connections import SessionPresence
from loommux.resource.liveness import LeaseStanding, assess_lease
from loommux.resource.model import ClientLease

PRIVATE_WINDOW = 4800
NAMED_WINDOW = 86400
GRACE = 60


def make_lease(
    mode: LeaseMode,
    *,
    renewed_at: float = 1000,
    requested_at: float = 1000,
) -> ClientLease:
    stamp = datetime(2026, 10, 20, tzinfo=UTC)
    return ClientLease(
        lease_id="lease",
        client=LeaseClient("session", "client-session"),
        policy=LeasePolicy(
            mode=mode,
            generation=1,
            private_activity_timeout_seconds=PRIVATE_WINDOW,
            named_activity_timeout_seconds=NAMED_WINDOW,
            heartbeat_interval_seconds=15,
            heartbeat_timeout_seconds=60,
            connection_grace_seconds=GRACE,
        ),
        created_at=stamp,
        last_seen_at=stamp,
        renewed_at=renewed_at,
        requested_at=requested_at,
    )


@pytest.mark.parametrize(
    ("mode", "named", "window"),
    [
        (LeaseMode.ACTIVITY, False, PRIVATE_WINDOW),
        (LeaseMode.ACTIVITY, True, NAMED_WINDOW),
        (LeaseMode.HEARTBEAT, False, 60),
        (LeaseMode.HEARTBEAT, True, 60),
    ],
)
def test_renewable_leases_ignore_the_connection(
    mode: LeaseMode,
    named: bool,
    window: float,
) -> None:
    presence = SessionPresence(first_seen_at=900, open_streams=1)

    verdict = assess_lease(make_lease(mode), presence, named=named, now=1000)

    assert verdict.standing is LeaseStanding.RENEWABLE
    assert verdict.expires_at == 1000 + window
    assert verdict.window_seconds == window
    assert verdict.remaining_seconds(1010) == window - 10
    assert verdict.grace_remaining_seconds(1010) is None
    assert not verdict.expired(1000 + window - 1)
    assert verdict.expired(1000 + window)


@pytest.mark.parametrize(
    "presence",
    [
        SessionPresence(first_seen_at=0, open_streams=1),
        SessionPresence(first_seen_at=0, open_streams=2, closed_at=5),
        SessionPresence(first_seen_at=0, process_bound=True),
    ],
)
def test_open_stream_or_process_lifetime_holds_the_lease_indefinitely(
    presence: SessionPresence,
) -> None:
    lease = make_lease(LeaseMode.CONNECTION, renewed_at=0, requested_at=0)

    verdict = assess_lease(lease, presence, named=False, now=10 * PRIVATE_WINDOW)

    assert verdict.standing is LeaseStanding.CONNECTED
    assert not verdict.standing.alert
    assert verdict.remaining_seconds(10 * PRIVATE_WINDOW) is None
    assert not verdict.expired(float("inf"))


def test_a_session_that_never_opens_a_stream_degrades_after_the_grace() -> None:
    lease = make_lease(LeaseMode.CONNECTION, renewed_at=1030, requested_at=1030)
    presence = SessionPresence(first_seen_at=1000)

    awaiting = assess_lease(lease, presence, named=False, now=1059)
    unbound = assess_lease(lease, presence, named=False, now=1060)

    assert awaiting.standing is LeaseStanding.AWAITING_STREAM
    assert not awaiting.standing.alert
    assert awaiting.expires_at is None
    assert awaiting.grace_remaining_seconds(1059) == 1
    assert unbound.standing is LeaseStanding.UNBOUND
    assert unbound.standing.alert
    assert unbound.expires_at == 1030 + PRIVATE_WINDOW
    assert unbound.window_seconds == PRIVATE_WINDOW


@pytest.mark.parametrize("named", [False, True])
def test_a_departed_session_leaves_its_main_kernel_waiting_the_named_window(named: bool) -> None:
    lease = make_lease(LeaseMode.CONNECTION, renewed_at=1990, requested_at=1500)
    presence = SessionPresence(first_seen_at=1000, closed_at=2000)

    reconnecting = assess_lease(lease, presence, named=named, now=2030)
    disconnected = assess_lease(lease, presence, named=named, now=2060)

    assert reconnecting.standing is LeaseStanding.RECONNECTING
    assert reconnecting.grace_remaining_seconds(2030) == 30
    assert not reconnecting.expired(2059)
    # Every main kernel waits the same window, counted from the lease's last
    # renewal, and the departure is ordinary, so nothing alerts.
    assert disconnected.standing is LeaseStanding.DISCONNECTED
    assert not disconnected.standing.alert
    assert disconnected.window_seconds == NAMED_WINDOW
    assert disconnected.expires_at == 1990 + NAMED_WINDOW
    assert disconnected.grace_remaining_seconds(2060) is None
    assert not disconnected.expired(1990 + NAMED_WINDOW - 1)
    assert disconnected.expired(1990 + NAMED_WINDOW)


def test_a_request_after_the_drop_proves_the_client_lost_only_its_stream() -> None:
    lease = make_lease(LeaseMode.CONNECTION, renewed_at=2010, requested_at=2010)
    presence = SessionPresence(first_seen_at=1000, closed_at=2000)

    verdict = assess_lease(lease, presence, named=False, now=2060)

    assert verdict.standing is LeaseStanding.STREAM_LOST
    assert verdict.standing.alert
    assert verdict.expires_at == 2010 + PRIVATE_WINDOW
    assert not verdict.expired(2060)
