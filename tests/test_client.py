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


def test_client_forwards_discovery_and_complete_tool_call_surface(monkeypatch) -> None:
    class RecordingClient:
        instance = None

        def __init__(self, _transport) -> None:
            type(self).instance = self
            self.calls = []
            self.exited = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            self.exited = True
            return None

        async def list_tools(self, max_pages=250):
            self.calls.append(("list_tools", max_pages))
            return ["tool"]

        async def call_tool(self, name, arguments, **options):
            self.calls.append(("call_tool", name, arguments, options))
            return "task-or-result"

        async def ping(self):
            self.calls.append(("ping",))
            return True

    async def fetch_policy(self):
        return RemoteLeasePolicy(
            mode="activity",
            generation=1,
            private_activity_timeout_seconds=30,
            named_activity_timeout_seconds=90,
            heartbeat_interval_seconds=15,
            heartbeat_timeout_seconds=60,
        )

    monkeypatch.setattr(client_module, "Client", RecordingClient)
    monkeypatch.setattr(LeaseAwareClient, "_fetch_policy", fetch_policy)

    async def scenario() -> None:
        client = LeaseAwareClient("http://127.0.0.1:8801/mcp", "test")
        async with client:
            assert await client.list_tools(max_pages=7) == ["tool"]
            assert (
                await client.call_tool(
                    "run",
                    {"value": 1},
                    version="v2",
                    timeout=3,
                    progress_handler="progress",
                    raise_on_error=False,
                    meta={"trace": "x"},
                    task=True,
                    task_id="task-1",
                    ttl=9000,
                )
                == "task-or-result"
            )
            assert await client.ping()

        assert RecordingClient.instance.calls == [
            ("list_tools", 7),
            (
                "call_tool",
                "run",
                {"value": 1},
                {
                    "version": "v2",
                    "timeout": 3,
                    "progress_handler": "progress",
                    "raise_on_error": False,
                    "meta": {"trace": "x"},
                    "task": True,
                    "task_id": "task-1",
                    "ttl": 9000,
                },
            ),
            ("ping",),
        ]
        assert RecordingClient.instance.exited

    asyncio.run(scenario())


def test_client_operations_require_an_active_context() -> None:
    async def scenario() -> None:
        client = LeaseAwareClient("http://127.0.0.1:8801/mcp", "test")
        with pytest.raises(RuntimeError, match="not inside its async context"):
            await client.list_tools()
        with pytest.raises(RuntimeError, match="not inside its async context"):
            await client.call_tool("run")
        with pytest.raises(RuntimeError, match="not inside its async context"):
            await client.ping()

    asyncio.run(scenario())
