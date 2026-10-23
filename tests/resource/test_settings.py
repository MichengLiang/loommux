import pytest

from loommux.resource import LeaseMode
from loommux.resource.settings import ResourceServerSettings


def test_unset_names_use_the_declared_lease_defaults() -> None:
    """A config.py that sets nothing must produce the durations the project declares."""

    settings = ResourceServerSettings.from_values({})

    # The first assertion guards the two declaration sites against drifting
    # apart; the second pins the value the documentation publishes.
    assert settings == ResourceServerSettings()
    assert settings.private_activity_timeout_seconds == 80 * 60
    assert settings.named_activity_timeout_seconds == 24 * 60 * 60


def test_settings_read_all_resource_lease_values() -> None:
    settings = ResourceServerSettings.from_values(
        {
            "lease_mode": "heartbeat",
            "private_activity_timeout_seconds": 3,
            "named_activity_timeout_seconds": 9,
            "heartbeat_interval_seconds": 1,
            "heartbeat_timeout_seconds": 4,
            "sweep_interval_seconds": 0.5,
            "orphan_grace_seconds": 2,
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
    [0, -1, float("nan"), float("inf"), "10", True],
)
def test_settings_reject_anything_but_positive_finite_seconds(value: object) -> None:
    with pytest.raises(ValueError, match="sweep_interval_seconds"):
        ResourceServerSettings.from_values({"sweep_interval_seconds": value})


def test_settings_reject_unknown_lease_mode() -> None:
    with pytest.raises(ValueError, match="'activity', 'heartbeat', or 'connection'"):
        ResourceServerSettings.from_values({"lease_mode": "other"})


def test_settings_read_the_connection_mode_and_its_grace() -> None:
    settings = ResourceServerSettings.from_values(
        {
            "lease_mode": " Connection ",
            "connection_grace_seconds": 12.5,
        }
    )

    assert settings.lease_mode is LeaseMode.CONNECTION
    assert settings.connection_grace_seconds == 12.5
    assert ResourceServerSettings.from_values({}).connection_grace_seconds == 60


def test_settings_reject_invalid_heartbeat_relationship() -> None:
    with pytest.raises(ValueError, match="must be greater"):
        ResourceServerSettings.from_values(
            {
                "heartbeat_interval_seconds": 5,
                "heartbeat_timeout_seconds": 5,
            }
        )
