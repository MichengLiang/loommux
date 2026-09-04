import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from loommux.resource import (
    KernelResourceManager,
    LeaseClient,
    LeaseMode,
    LeasePolicy,
    ResourceAddress,
    ResourceBusyError,
    ResourceLifecycle,
)


class FakeKernel:
    def __init__(self, pid: int) -> None:
        self.pid = pid


class FakeSession:
    next_pid = 4000
    created = 0
    start_barrier: threading.Barrier | None = None

    def __init__(self) -> None:
        type(self).created += 1
        type(self).next_pid += 1
        self.kernel = FakeKernel(type(self).next_pid)
        self.current_execution: int | None = None
        self.recent_execution: int | None = None
        self.executions: dict[int, object] = {}
        self.closed = False

    def start_workspace(
        self,
        workspace: Path,
        workspace_resolution: str,
    ) -> dict[str, Any]:
        barrier = type(self).start_barrier
        if barrier is not None:
            barrier.wait(timeout=2)
        return {
            "ok": True,
            "workspace": str(workspace),
            "workspace_resolution": workspace_resolution,
        }

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
        type(self).next_pid += 1
        self.kernel = FakeKernel(type(self).next_pid)
        return {"ok": True, "status": "restarted"}


def address(key: str) -> ResourceAddress:
    return ResourceAddress(
        key=key,
        display_name=key,
        scope="named",
        shared=True,
    )


def test_same_address_shares_one_provisioning_task(tmp_path: Path) -> None:
    async def scenario() -> None:
        FakeSession.created = 0
        manager = KernelResourceManager(
            tmp_path,
            "launch_cwd",
            session_factory=FakeSession,
        )
        first, second = await asyncio.gather(
            manager.get_or_create(address("same")),
            manager.get_or_create(address("same")),
        )

        assert first is second
        assert FakeSession.created == 1
        await manager.stop()
        assert first.lifecycle is ResourceLifecycle.STOPPED
        assert first.session.closed

    asyncio.run(scenario())


def test_different_addresses_can_provision_concurrently(tmp_path: Path) -> None:
    async def scenario() -> None:
        FakeSession.created = 0
        FakeSession.start_barrier = threading.Barrier(2)
        manager = KernelResourceManager(
            tmp_path,
            "launch_cwd",
            session_factory=FakeSession,
        )
        try:
            first, second = await asyncio.wait_for(
                asyncio.gather(
                    manager.get_or_create(address("first")),
                    manager.get_or_create(address("second")),
                ),
                timeout=3,
            )
            assert first is not second
            assert first.kernel_pid != second.kernel_pid
        finally:
            FakeSession.start_barrier = None
            await manager.stop()

    asyncio.run(scenario())


def test_recycle_refuses_busy_resource_without_force(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(
            tmp_path,
            "launch_cwd",
            session_factory=FakeSession,
        )
        resource = await manager.get_or_create(address("busy"))
        resource.session.current_execution = 1

        with pytest.raises(ResourceBusyError):
            await manager.recycle(resource.resource_id)

        recycled = await manager.recycle(resource.resource_id, force=True)
        assert recycled.lifecycle is ResourceLifecycle.STOPPED
        assert await manager.snapshot() == []

    asyncio.run(scenario())


def test_dead_kernel_is_recovered_inside_the_same_logical_resource(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(
            tmp_path,
            "launch_cwd",
            session_factory=FakeSession,
        )
        resource = await manager.get_or_create(address("recover"))
        resource_id = resource.resource_id
        previous_pid = resource.kernel_pid
        resource.session.kernel = None

        async with manager.operation(
            address("recover"),
            LeaseClient("client", "client"),
            LeasePolicy(
                mode=LeaseMode.ACTIVITY,
                generation=1,
                private_activity_timeout_seconds=30,
                named_activity_timeout_seconds=30,
                heartbeat_interval_seconds=10,
                heartbeat_timeout_seconds=20,
            ),
        ) as recovered:
            assert recovered.resource_id == resource_id
            assert recovered.kernel_pid != previous_pid
            assert recovered.lifecycle is ResourceLifecycle.RUNNING
        await manager.stop()

    asyncio.run(scenario())
