import pytest

from loommux.client import RemoteLeasePolicy


def test_remote_policy_validation() -> None:
    policy = RemoteLeasePolicy.from_payload(
        {
            "mode": "heartbeat",
            "generation": 3,
            "private_activity_timeout_seconds": 30,
            "named_activity_timeout_seconds": 90,
            "heartbeat_interval_seconds": 15,
            "heartbeat_timeout_seconds": 60,
        }
    )

    assert policy.mode == "heartbeat"
    assert policy.generation == 3


def test_remote_policy_rejects_an_impossible_heartbeat_window() -> None:
    with pytest.raises(ValueError, match="heartbeat window"):
        RemoteLeasePolicy.from_payload(
            {
                "mode": "heartbeat",
                "generation": 1,
                "private_activity_timeout_seconds": 30,
                "named_activity_timeout_seconds": 90,
                "heartbeat_interval_seconds": 15,
                "heartbeat_timeout_seconds": 15,
            }
        )
