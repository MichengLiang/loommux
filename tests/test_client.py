import asyncio

import pytest

import loommux.client as client_module
from loommux.client import LeaseAwareClient, RemoteLeasePolicy


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


def test_failed_client_entry_closes_partial_client(monkeypatch) -> None:
    class FailingClient:
        instance = None

        def __init__(self, _transport) -> None:
            type(self).instance = self
            self.closed = False

        async def __aenter__(self):
            raise RuntimeError("connect failed")

        async def close(self) -> None:
            self.closed = True

    async def fetch_policy(self):
        return RemoteLeasePolicy(
            mode="activity",
            generation=1,
            private_activity_timeout_seconds=30,
            named_activity_timeout_seconds=90,
            heartbeat_interval_seconds=15,
            heartbeat_timeout_seconds=60,
        )

    monkeypatch.setattr(client_module, "Client", FailingClient)
    monkeypatch.setattr(LeaseAwareClient, "_fetch_policy", fetch_policy)

    async def scenario() -> None:
        client = LeaseAwareClient("http://127.0.0.1:8801/mcp", "test")
        with pytest.raises(RuntimeError, match="connect failed"):
            await client.__aenter__()
        assert FailingClient.instance.closed
        assert client._client is None

    asyncio.run(scenario())


def test_client_rejects_nested_entry() -> None:
    async def scenario() -> None:
        client = LeaseAwareClient("http://127.0.0.1:8801/mcp", "test")
        client._client = object()
        with pytest.raises(RuntimeError, match="already active"):
            await client.__aenter__()

    asyncio.run(scenario())
