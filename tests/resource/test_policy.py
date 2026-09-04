import asyncio

import pytest

from loommux.resource import LeaseMode, LeasePolicyManager


def test_policy_updates_are_versioned_and_idempotent() -> None:
    async def scenario() -> None:
        manager = LeasePolicyManager(
            initial_mode=LeaseMode.ACTIVITY,
            private_activity_timeout_seconds=30,
            named_activity_timeout_seconds=90,
            heartbeat_interval_seconds=15,
            heartbeat_timeout_seconds=60,
        )
        first = await manager.current()
        unchanged = await manager.update(
            mode=LeaseMode.ACTIVITY,
            private_activity_timeout_seconds=30,
            named_activity_timeout_seconds=90,
            heartbeat_interval_seconds=15,
            heartbeat_timeout_seconds=60,
        )
        second = await manager.update(
            mode=LeaseMode.HEARTBEAT,
            private_activity_timeout_seconds=45,
            named_activity_timeout_seconds=120,
            heartbeat_interval_seconds=10,
            heartbeat_timeout_seconds=50,
        )

        assert unchanged is first
        assert second.generation == 2
        assert await manager.generation(1) is first
        assert second.timeout_for(shared=False) == 50
        assert second.timeout_for(shared=True) == 50

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("interval", "timeout"),
    [(0, 1), (1, 1), (2, 1), (float("inf"), float("inf"))],
)
def test_policy_rejects_invalid_heartbeat_windows(
    interval: float,
    timeout: float,
) -> None:
    with pytest.raises(ValueError):
        LeasePolicyManager(
            heartbeat_interval_seconds=interval,
            heartbeat_timeout_seconds=timeout,
        )
