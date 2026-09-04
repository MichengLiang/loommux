import asyncio
from pathlib import Path
from typing import Any

from loommux.resource import (
    KernelResourceManager,
    LeaseClient,
    LeaseMode,
    LeasePolicy,
    ResourceAddress,
    ResourceLifecycle,
)


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
) -> LeasePolicy:
    return LeasePolicy(
        mode=mode,
        generation=1,
        private_activity_timeout_seconds=timeout,
        named_activity_timeout_seconds=timeout,
        heartbeat_interval_seconds=0.01,
        heartbeat_timeout_seconds=timeout,
    )


def make_address() -> ResourceAddress:
    return ResourceAddress(
        key="named:shared",
        display_name="shared",
        scope="named_shared",
        shared=True,
    )


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
        address = make_address()
        policy = make_policy()
        async with manager.operation(address, make_client("first"), policy):
            pass
        await asyncio.sleep(0.02)
        async with manager.operation(address, make_client("second"), policy):
            pass
        await asyncio.sleep(0.02)

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

        assert not await manager.renew_client_lease("named:missing", "client")
        assert await manager.snapshot() == []

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
        async with manager.operation(
            make_address(),
            make_client("only"),
            make_policy(),
        ) as resource:
            pass

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
        async with manager.operation(
            make_address(),
            make_client("only"),
            make_policy(),
        ) as resource:
            resource.session.current_execution = 1

        await asyncio.sleep(0.04)
        assert await manager.sweep_once() == 0
        assert resource.lifecycle is ResourceLifecycle.ORPHANED

        await asyncio.sleep(0.04)
        assert await manager.sweep_once() == 1
        assert resource.lifecycle is ResourceLifecycle.STOPPED

    asyncio.run(scenario())
