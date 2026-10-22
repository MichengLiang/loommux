import asyncio
from pathlib import Path
from time import monotonic
from typing import Any

from loommux.resource import (
    KernelRequest,
    KernelResourceManager,
    LeaseClient,
    LeaseMode,
    LeasePolicy,
    RequestMode,
    ResourceLifecycle,
)
from loommux.resource.connections import ConnectionRegistry
from loommux.resource.liveness import LeaseStanding


class FakeKernel:
    pid = 5001


class FakeSession:
    def __init__(self) -> None:
        self.kernel = FakeKernel()
        self.current_execution: int | None = None
        self.recent_execution: int | None = None
        self.executions: dict[int, object] = {}
        self.closed = False

    def start_workspace(
        self,
        workspace: Path,
        workspace_resolution: str,
        *,
        identity: object = None,
    ) -> dict[str, Any]:
        return {"ok": True}

    def close(self) -> None:
        self.closed = True
        self.kernel = None

    def status(self) -> dict[str, Any]:
        return {
            "kernel_started": self.kernel is not None,
            "kernel_pid": self.kernel.pid if self.kernel else None,
            "current_execution": self.current_execution,
            "recent_execution": self.recent_execution,
        }

    def restart(self) -> dict[str, Any]:
        self.kernel = FakeKernel()
        return {"ok": True, "status": "restarted"}


def make_policy(
    *,
    timeout: float = 0.03,
    mode: LeaseMode = LeaseMode.HEARTBEAT,
    grace: float | None = None,
) -> LeasePolicy:
    return LeasePolicy(
        mode=mode,
        generation=1,
        private_activity_timeout_seconds=timeout,
        named_activity_timeout_seconds=timeout,
        heartbeat_interval_seconds=0.01,
        heartbeat_timeout_seconds=timeout,
        connection_grace_seconds=timeout if grace is None else grace,
    )


def make_request() -> KernelRequest:
    return KernelRequest("shared", RequestMode.JOIN)


def make_client(client_id: str) -> LeaseClient:
    return LeaseClient(client_id, f"client-{client_id}")


def test_shared_resource_tracks_client_leases_independently(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(
            tmp_path,
            "launch_cwd",
            session_factory=FakeSession,
            sweep_interval_seconds=60,
        )
        request = make_request()
        policy = make_policy()
        async with manager.operation(make_client("first"), policy, request=request) as admitted:
            resource = admitted.resource
        # Drive the first lease past its window directly. Sleeping for a
        # 30-ms lease makes this state-machine test scheduler-sensitive on
        # hosted Windows runners.
        resource.client_leases["first"].renewed_at -= 3600
        async with manager.operation(make_client("second"), policy, request=request):
            pass

        assert await manager.sweep_once() == 0
        [snapshot] = await manager.snapshot()
        assert snapshot["lease_count"] == 1
        assert snapshot["leases"][0]["client_id"] == "second"
        await manager.stop()

    asyncio.run(scenario())


def test_protocol_renewal_does_not_create_a_resource(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(
            tmp_path,
            "launch_cwd",
            session_factory=FakeSession,
        )

        assert not await manager.renew_client_lease("client")
        assert await manager.snapshot() == []

    asyncio.run(scenario())


def test_protocol_renewal_keeps_the_lease_of_a_resource_whose_kernel_exited(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(
            tmp_path,
            "launch_cwd",
            session_factory=FakeSession,
        )
        request = make_request()
        client = make_client("connected")
        async with manager.operation(client, make_policy(), request=request) as admitted:
            resource = admitted.resource
        resource.session.kernel = None
        previous_renewal = resource.client_leases[client.client_id].renewed_at

        await asyncio.sleep(0.01)

        assert await manager.renew_client_lease(client.client_id)
        assert (
            resource.client_leases[client.client_id].renewed_at
            > previous_renewal
        )
        await manager.stop()

    asyncio.run(scenario())


def test_health_observation_does_not_renew_the_client_lease(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(
            tmp_path,
            "launch_cwd",
            session_factory=FakeSession,
        )
        request = make_request()
        client = make_client("observed")
        async with manager.operation(client, make_policy(), request=request) as admitted:
            resource = admitted.resource
        renewal = resource.client_leases[client.client_id].renewed_at

        health = await manager.health(resource.resource_id)

        assert health["ok"] is True
        assert resource.client_leases[client.client_id].renewed_at == renewal
        await manager.stop()

    asyncio.run(scenario())


def test_idle_resource_is_reclaimed_after_last_lease_expires(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(
            tmp_path,
            "launch_cwd",
            session_factory=FakeSession,
            orphan_grace_seconds=1,
        )
        async with manager.operation(make_client("only"), make_policy(), request=make_request()) as admitted:
            resource = admitted.resource

        await asyncio.sleep(0.04)
        assert await manager.sweep_once() == 1
        assert resource.lifecycle is ResourceLifecycle.STOPPED
        assert resource.session.closed

    asyncio.run(scenario())


def test_running_orphan_receives_grace_before_forced_reclamation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(
            tmp_path,
            "launch_cwd",
            session_factory=FakeSession,
            orphan_grace_seconds=0.03,
        )
        async with manager.operation(make_client("only"), make_policy(), request=make_request()) as admitted:
            resource = admitted.resource
            resource.session.current_execution = 1

        await asyncio.sleep(0.04)
        assert await manager.sweep_once() == 0
        assert resource.lifecycle is ResourceLifecycle.ORPHANED

        await asyncio.sleep(0.04)
        assert await manager.sweep_once() == 1
        assert resource.lifecycle is ResourceLifecycle.STOPPED

    asyncio.run(scenario())


def connection_manager(tmp_path: Path, registry: ConnectionRegistry) -> KernelResourceManager:
    return KernelResourceManager(
        tmp_path,
        "launch_cwd",
        session_factory=FakeSession,
        connections=registry,
        orphan_grace_seconds=1,
    )


def test_open_stream_holds_a_connection_lease_past_its_window_until_the_stream_departs(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        registry = ConnectionRegistry()
        manager = connection_manager(tmp_path, registry)
        client = make_client("streaming")
        registry.stream_opened(client.client_id)
        policy = make_policy(mode=LeaseMode.CONNECTION, grace=0.03)
        async with manager.operation(client, policy, request=make_request()) as admitted:
            resource = admitted.resource
        lease = resource.client_leases[client.client_id]
        lease.renewed_at -= 3600
        lease.requested_at -= 3600

        assert await manager.sweep_once() == 0
        [snapshot] = await manager.snapshot()
        assert snapshot["leases"][0]["standing"] == "connected"
        assert snapshot["leases"][0]["remaining_seconds"] is None
        assert snapshot["leases"][0]["alert"] is False

        registry.stream_closed(client.client_id, now=monotonic() - 1)

        assert await manager.sweep_once() == 1
        assert resource.lifecycle is ResourceLifecycle.STOPPED
        assert client.client_id not in registry

    asyncio.run(scenario())


def test_a_departed_sessions_main_kernel_waits_the_named_window_then_leaves(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        registry = ConnectionRegistry()
        manager = connection_manager(tmp_path, registry)
        client = make_client("departed")
        registry.stream_opened(client.client_id)
        policy = make_policy(mode=LeaseMode.CONNECTION, timeout=60, grace=0.03)
        async with manager.operation(client, policy, request=make_request()) as admitted:
            resource = admitted.resource
        lease = resource.client_leases[client.client_id]
        # The client sent nothing after its stream closed: it left.
        lease.requested_at -= 3600
        registry.stream_closed(client.client_id, now=monotonic() - 1)

        assert await manager.sweep_once() == 0
        [snapshot] = await manager.snapshot()
        assert snapshot["leases"][0]["standing"] == "disconnected"
        assert snapshot["leases"][0]["alert"] is False
        assert snapshot["leases"][0]["timeout_seconds"] == 60
        assert 0 < snapshot["leases"][0]["remaining_seconds"] <= 60

        lease.renewed_at -= 3600
        assert await manager.sweep_once() == 1
        assert resource.lifecycle is ResourceLifecycle.STOPPED

    asyncio.run(scenario())


def test_connection_lease_without_a_stream_alerts_and_keeps_the_activity_window(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        registry = ConnectionRegistry()
        manager = connection_manager(tmp_path, registry)
        client = make_client("streamless")
        policy = make_policy(mode=LeaseMode.CONNECTION, timeout=60, grace=0.03)
        async with manager.operation(client, policy, request=make_request()) as admitted:
            resource = admitted.resource

        awaiting = await manager.client_verdict(client.client_id)
        registry.presence(client.client_id).first_seen_at -= 3600

        assert awaiting is not None
        assert awaiting.standing is LeaseStanding.AWAITING_STREAM
        assert await manager.sweep_once() == 0
        [snapshot] = await manager.snapshot()
        assert snapshot["leases"][0]["standing"] == "unbound"
        assert snapshot["leases"][0]["alert"] is True
        assert snapshot["leases"][0]["timeout_seconds"] == 60
        assert await manager.client_verdict("missing") is None
        await manager.stop()

    asyncio.run(scenario())


def test_ping_after_the_stream_drops_proves_the_client_lost_only_its_stream(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        registry = ConnectionRegistry()
        manager = connection_manager(tmp_path, registry)
        client = make_client("pinging")
        registry.stream_opened(client.client_id)
        policy = make_policy(mode=LeaseMode.CONNECTION, timeout=60, grace=0.03)
        async with manager.operation(client, policy, request=make_request()) as admitted:
            resource = admitted.resource
        resource.client_leases[client.client_id].requested_at -= 3600
        registry.stream_closed(client.client_id, now=monotonic() - 1)

        assert await manager.renew_client_lease(client.client_id)
        verdict = await manager.client_verdict(client.client_id)

        assert verdict is not None
        assert verdict.standing is LeaseStanding.STREAM_LOST
        assert await manager.sweep_once() == 0
        await manager.stop()

    asyncio.run(scenario())


def test_sweep_forgets_departed_sessions_that_never_held_a_lease(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        registry = ConnectionRegistry()
        manager = connection_manager(tmp_path, registry)
        registry.stream_opened("visitor")
        registry.stream_closed("visitor")
        registry.stream_opened("present")

        await manager.sweep_once()

        assert "visitor" not in registry
        assert "present" in registry
        await manager.stop()

    asyncio.run(scenario())


def test_sweep_logs_each_entry_into_an_alert_once(tmp_path: Path, caplog) -> None:
    async def scenario() -> None:
        registry = ConnectionRegistry()
        manager = connection_manager(tmp_path, registry)
        client = make_client("streamless")
        policy = make_policy(mode=LeaseMode.CONNECTION, timeout=60, grace=0.03)
        async with manager.operation(client, policy, request=make_request()) as admitted:
            resource = admitted.resource
        registry.presence(client.client_id).first_seen_at -= 3600

        with caplog.at_level("WARNING", logger="loommux.resource.manager"):
            await manager.sweep_once()
            await manager.sweep_once()
            registry.stream_opened(client.client_id)
            await manager.sweep_once()
            registry.stream_closed(client.client_id, now=monotonic() - 1)
            await manager.sweep_once()

        lease = resource.client_leases[client.client_id]
        alerts = [record.getMessage() for record in caplog.records]
        assert len(alerts) == 2
        assert "client-streamless on shared is unbound" in alerts[0]
        assert "60-second activity window" in alerts[0]
        assert "is stream_lost" in alerts[1]
        assert lease.reported_alert is LeaseStanding.STREAM_LOST
        await manager.stop()

    asyncio.run(scenario())
