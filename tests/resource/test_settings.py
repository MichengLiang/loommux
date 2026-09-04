import pytest

from loommux.resource import LeaseMode
from loommux.resource.settings import ResourceServerSettings


def test_settings_read_all_resource_lease_values() -> None:
    settings = ResourceServerSettings.from_environ(
        {
            "LOOMMUX_LEASE_MODE": "heartbeat",
            "LOOMMUX_PRIVATE_TTL_SECONDS": "3",
            "LOOMMUX_NAMED_TTL_SECONDS": "9",
            "LOOMMUX_HEARTBEAT_INTERVAL_SECONDS": "1",
            "LOOMMUX_HEARTBEAT_TIMEOUT_SECONDS": "4",
            "LOOMMUX_SWEEP_INTERVAL_SECONDS": "0.5",
            "LOOMMUX_ORPHAN_GRACE_SECONDS": "2",
        }
    )

    assert settings.lease_mode is LeaseMode.HEARTBEAT
    assert settings.private_activity_timeout_seconds == 3
    assert settings.named_activity_timeout_seconds == 9
    assert settings.heartbeat_interval_seconds == 1
    assert settings.heartbeat_timeout_seconds == 4
    assert settings.sweep_interval_seconds == 0.5
    assert settings.orphan_grace_seconds == 2


@pytest.mark.parametrize(
    "value",
    ["0", "-1"],
)
def test_settings_reject_non_positive_durations(value: str) -> None:
    with pytest.raises(ValueError):
        ResourceServerSettings.from_environ(
            {"LOOMMUX_SWEEP_INTERVAL_SECONDS": value}
        )
