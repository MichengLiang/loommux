import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from loommux.resource import (
    ClientLease,
    KernelResourceManager,
    LeaseClient,
    LeaseMode,
    LeasePolicy,
    ResourceAddress,
    ResourceBusyError,
    ResourceLifecycle,
    ResourceNotFoundError,
)
from loommux.resource.model import KernelResource


class FakeKernel:
    def __init__(self, pid: int) -> None:
        self.pid = pid


class FakeSession:
    next_pid = 4000
    created = 0
    start_barrier: threading.Barrier | None = None
    identities: list[Any] = []

    def __init__(self) -> None:
        type(self).created += 1
        type(self).next_pid += 1
        self.kernel = FakeKernel(type(self).next_pid)
        self.current_execution: int | None = None
        self.recent_execution: int | None = None
        self.executions: dict[int, object] = {}
        self.closed = False
        self.interrupt_count = 0
        self.restarting = False

    def start_workspace(
        self,
        workspace: Path,
        workspace_resolution: str,
        *,
        identity: object = None,
    ) -> dict[str, Any]:
        type(self).identities.append(identity)
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
            "kernel_restarting": self.restarting,
            "kernel_pid": self.kernel.pid if self.kernel else None,
            "current_execution": self.current_execution,
            "recent_execution": self.recent_execution,
        }

    def restart(self) -> dict[str, Any]:
        type(self).next_pid += 1
        self.kernel = FakeKernel(type(self).next_pid)
        return {"ok": True, "status": "restarted"}

    def interrupt(self) -> dict[str, Any]:
        self.interrupt_count += 1
        return {"ok": True, "status": "idle"}


def address(key: str) -> ResourceAddress:
    return ResourceAddress(
        key=key,
        display_name=key,
        scope="named",
        shared=True,
    )


def policy() -> LeasePolicy:
    return LeasePolicy(
        mode=LeaseMode.ACTIVITY,
        generation=1,
        private_activity_timeout_seconds=30,
        named_activity_timeout_seconds=30,
        heartbeat_interval_seconds=10,
        heartbeat_timeout_seconds=20,
        connection_grace_seconds=20,
    )


def private_address(name: str) -> ResourceAddress:
    return ResourceAddress(key=f"session:{name}", display_name=name, scope="session_private", shared=False)


def test_kernels_opened_by_name_live_as_long_as_a_leased_resource_reaches_them(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(tmp_path, "launch_cwd", session_factory=FakeSession)
        root = await manager.get_or_create(private_address("agent"))
        root.client_leases["client"] = ClientLease.create(LeaseClient("client", "client"), policy())

        x = await manager.ensure_held("x", root.resource_id)
        y = await manager.ensure_held("y", x.resource_id)
        assert await manager.ensure_held("y", y.resource_id) is y
        assert await manager.ensure_held("x", y.resource_id) is x
        assert x.held_by == {root.resource_id, y.resource_id}
        assert y.held_by == {x.resource_id}
        assert (await manager.snapshot_one(x.resource_id))["held_by"] == ["agent", "y"]

        assert await manager.sweep_once() == 0
        assert {x.lifecycle, y.lifecycle} == {ResourceLifecycle.RUNNING}

        # The client leaves: x and y still hold each other, and nothing
        # leased reaches either, so the cycle is released with its root.
        del root.client_leases["client"]
        assert await manager.sweep_once() == 3
        assert {root.lifecycle, x.lifecycle, y.lifecycle} == {ResourceLifecycle.STOPPED}
        await manager.stop()

    asyncio.run(scenario())


def test_opening_by_name_needs_a_registered_holder_and_never_holds_a_private_kernel(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(tmp_path, "launch_cwd", session_factory=FakeSession)
        agent = await manager.get_or_create(private_address("agent"))
        other = await manager.get_or_create(private_address("other"))

        with pytest.raises(ResourceNotFoundError, match="only a kernel can open one"):
            await manager.ensure_held("lab", None)
        with pytest.raises(ResourceNotFoundError, match="holder resource was not found"):
            await manager.ensure_held("lab", "retired-holder")
        assert await manager.ensure_held("other", agent.resource_id) is other
        assert await manager.ensure_held(other.resource_id, None) is other
        assert other.held_by == set()
        await manager.stop()

    asyncio.run(scenario())


def test_a_holder_reopening_an_orphaned_kernel_returns_it_to_running(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(tmp_path, "launch_cwd", session_factory=FakeSession, orphan_grace_seconds=60)
        agent = await manager.get_or_create(private_address("agent"))
        agent.client_leases["client"] = ClientLease.create(LeaseClient("client", "client"), policy())
        lab = await manager.ensure_held("lab", agent.resource_id)
        lab.session.current_execution = 1
        lab.held_by.clear()

        assert await manager.sweep_once() == 0
        assert lab.lifecycle is ResourceLifecycle.ORPHANED
        await manager.ensure_held("lab", agent.resource_id)
        assert lab.lifecycle is ResourceLifecycle.RUNNING
        assert lab.orphaned_at is None
        await manager.stop()

    asyncio.run(scenario())


def test_retiring_a_holder_removes_its_holding_edges(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(tmp_path, "launch_cwd", session_factory=FakeSession)
        agent = await manager.get_or_create(private_address("agent"))
        lab = await manager.ensure_held("lab", agent.resource_id)

        await manager.recycle(agent.resource_id)

        assert lab.held_by == set()
        await manager.stop()

    asyncio.run(scenario())


def test_program_operations_count_as_work_without_creating_a_lease(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(tmp_path, "launch_cwd", session_factory=FakeSession)
        lab = await manager.get_or_create(address("lab"))

        async with manager.program_operation(lab.resource_id) as admitted:
            assert admitted is lab
            assert lab.is_busy
        assert lab.active_operations == 0
        assert lab.client_leases == {}
        with pytest.raises(ResourceNotFoundError):
            async with manager.program_operation("missing"):
                pass
        await manager.stop()

    asyncio.run(scenario())


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


def test_a_kernel_is_told_its_resource_id_name_and_control_url_at_start(tmp_path: Path) -> None:
    async def scenario() -> None:
        FakeSession.identities = []
        manager = KernelResourceManager(
            tmp_path,
            "launch_cwd",
            session_factory=FakeSession,
            control_url="http://127.0.0.1:9999",
        )
        resource = await manager.get_or_create(address("lab"))
        [identity] = FakeSession.identities

        assert identity.kernel_id == resource.resource_id
        assert identity.name == "lab"
        assert identity.control_url == "http://127.0.0.1:9999"
        assert (await manager.snapshot_one(resource.resource_id))["resource_id"] == resource.resource_id
        await manager.stop()

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
            policy(),
        ) as recovered:
            assert recovered.resource_id == resource_id
            assert recovered.kernel_pid != previous_pid
            assert recovered.lifecycle is ResourceLifecycle.RUNNING
        await manager.stop()

    asyncio.run(scenario())


def test_manager_control_operations_and_bulk_recycling(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(
            tmp_path,
            "launch_cwd",
            session_factory=FakeSession,
        )
        first = await manager.get_or_create(address("first-control"))
        second = await manager.get_or_create(address("second-control"))

        interrupted = await manager.interrupt(first.resource_id)
        restarted = await manager.restart(first.resource_id)
        health = await manager.health(first.resource_id)

        assert interrupted["status"] == "idle"
        assert restarted["status"] == "restarted"
        assert health["ok"] is True
        assert health["kernel_pid"] == first.kernel_pid
        assert await manager.recycle_idle() == 2
        assert await manager.snapshot() == []

        busy = await manager.get_or_create(address("busy-control"))
        idle = await manager.get_or_create(address("idle-control"))
        busy.session.current_execution = 1
        assert await manager.recycle_idle() == 1
        assert idle.session.closed
        assert await manager.recycle_all() == 1
        assert busy.session.closed

    asyncio.run(scenario())


def test_unknown_resource_control_is_rejected(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(
            tmp_path,
            "launch_cwd",
            session_factory=FakeSession,
        )
        with pytest.raises(ResourceNotFoundError, match="resource was not found"):
            await manager.interrupt("missing")

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_manager_rejects_invalid_sweep_durations(
    tmp_path: Path,
    value: float,
) -> None:
    with pytest.raises(ValueError, match="positive finite"):
        KernelResourceManager(
            tmp_path,
            "launch_cwd",
            session_factory=FakeSession,
            sweep_interval_seconds=value,
        )


def test_restart_and_recycle_are_serialized_by_resource_lifecycle(
    tmp_path: Path,
) -> None:
    class BlockingRestartSession(FakeSession):
        restart_started = threading.Event()
        restart_release = threading.Event()

        def restart(self) -> dict[str, Any]:
            type(self).restart_started.set()
            assert type(self).restart_release.wait(timeout=2)
            return super().restart()

    async def scenario() -> None:
        manager = KernelResourceManager(
            tmp_path,
            "launch_cwd",
            session_factory=BlockingRestartSession,
        )
        resource = await manager.get_or_create(address("serialized"))
        restart = asyncio.create_task(manager.restart(resource.resource_id))
        assert await asyncio.to_thread(
            BlockingRestartSession.restart_started.wait,
            1,
        )
        recycle = asyncio.create_task(
            manager.recycle(resource.resource_id, force=True)
        )
        await asyncio.sleep(0.03)
        assert not recycle.done()

        BlockingRestartSession.restart_release.set()
        assert (await restart)["status"] == "restarted"
        assert (await recycle).lifecycle is ResourceLifecycle.STOPPED

    asyncio.run(scenario())


def test_detached_resource_cannot_be_restarted_after_waiting_for_lifecycle(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(
            tmp_path,
            "launch_cwd",
            session_factory=FakeSession,
        )
        resource = await manager.get_or_create(address("retired"))
        await resource.lifecycle_lock.acquire()
        restart = asyncio.create_task(manager.restart(resource.resource_id))
        await asyncio.sleep(0)

        async with manager._registry_lock:
            manager._detach_locked(resource)
            resource.lifecycle = ResourceLifecycle.CLOSING
        resource.lifecycle_lock.release()

        with pytest.raises(ResourceNotFoundError):
            await restart
        await asyncio.to_thread(resource.session.close)

    asyncio.run(scenario())


def test_observers_report_a_dead_kernel_without_changing_the_lifecycle(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(
            tmp_path,
            "launch_cwd",
            session_factory=FakeSession,
        )
        resource = await manager.get_or_create(address("dead"))
        resource.session.kernel = None

        [snapshot] = await manager.snapshot()
        health = await manager.health(resource.resource_id)

        assert snapshot["kernel_pid"] is None
        assert snapshot["kernel_state"] == "stopped"
        assert snapshot["lifecycle"] == "running"
        assert health["ok"] is False
        assert resource.lifecycle is ResourceLifecycle.RUNNING
        async with manager.operation(
            address("dead"),
            LeaseClient("client", "client"),
            policy(),
        ) as recovered:
            assert recovered is resource
            assert recovered.kernel_pid is not None
        await manager.stop()

    asyncio.run(scenario())


def test_observing_a_restart_gap_does_not_stall_the_next_operation(
    tmp_path: Path,
) -> None:
    class GappedRestartSession(FakeSession):
        # IPythonSession.restart drops the old kernel before the new one is
        # ready, so observers can read "no kernel" while the restart succeeds.
        in_gap = threading.Event()
        release = threading.Event()

        def restart(self) -> dict[str, Any]:
            self.kernel = None
            self.restarting = True
            type(self).in_gap.set()
            assert type(self).release.wait(timeout=2)
            result = super().restart()
            self.restarting = False
            return result

    async def scenario() -> None:
        manager = KernelResourceManager(
            tmp_path,
            "launch_cwd",
            session_factory=GappedRestartSession,
        )
        client = LeaseClient("client", "client")

        async def restart_tool() -> None:
            async with manager.operation(address("gap"), client, policy()) as resource:
                await asyncio.to_thread(resource.session.restart)

        restart = asyncio.create_task(restart_tool())
        assert await asyncio.to_thread(GappedRestartSession.in_gap.wait, 1)
        [snapshot] = await manager.snapshot()
        await manager.sweep_once()
        assert snapshot["kernel_pid"] is None
        assert snapshot["kernel_state"] == "restarting"
        GappedRestartSession.release.set()
        await restart

        # A gap reading once became a stored lifecycle that nothing reset, and
        # this operation then retried forever without yielding the event loop;
        # only the pytest-timeout thread can end such a test.
        async with manager.operation(address("gap"), client, policy()) as resource:
            assert resource.lifecycle is ResourceLifecycle.RUNNING
            assert resource.kernel_pid is not None
        [snapshot] = await manager.snapshot()
        assert snapshot["kernel_state"] == "running"
        await manager.stop()

    asyncio.run(scenario())


def test_a_kernel_replacing_operation_holds_off_recovery(tmp_path: Path) -> None:
    class CountingGappedSession(FakeSession):
        restarts = 0
        in_gap = threading.Event()
        release = threading.Event()

        def restart(self) -> dict[str, Any]:
            type(self).restarts += 1
            self.kernel = None
            type(self).in_gap.set()
            assert type(self).release.wait(timeout=2)
            return super().restart()

    async def scenario() -> None:
        manager = KernelResourceManager(
            tmp_path,
            "launch_cwd",
            session_factory=CountingGappedSession,
        )
        client = LeaseClient("client", "client")

        async def restart_tool() -> None:
            async with manager.operation(
                address("replace"),
                client,
                policy(),
                replaces_kernel=True,
            ) as resource:
                await asyncio.to_thread(resource.session.restart)

        async def other_tool() -> int | None:
            async with manager.operation(address("replace"), client, policy()) as resource:
                return resource.kernel_pid

        restart = asyncio.create_task(restart_tool())
        assert await asyncio.to_thread(CountingGappedSession.in_gap.wait, 1)
        other = asyncio.create_task(other_tool())
        await asyncio.sleep(0.05)
        assert not other.done()

        CountingGappedSession.release.set()
        await restart
        assert await other is not None
        assert CountingGappedSession.restarts == 1
        await manager.stop()

    asyncio.run(scenario())


def test_a_resource_retired_during_recovery_is_replaced_by_its_successor(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(
            tmp_path,
            "launch_cwd",
            session_factory=FakeSession,
        )
        retired = await manager.get_or_create(address("retired"))
        retired.session.kernel = None
        await retired.lifecycle_lock.acquire()
        call = asyncio.create_task(_enter(manager, "retired"))
        await asyncio.sleep(0.01)
        assert not call.done()

        async with manager._registry_lock:
            manager._detach_locked(retired)
            retired.lifecycle = ResourceLifecycle.CLOSING
        retired.lifecycle_lock.release()

        successor = await call
        assert successor is not retired
        assert successor.lifecycle is ResourceLifecycle.RUNNING
        await manager.stop()

    asyncio.run(scenario())


async def _enter(manager: KernelResourceManager, key: str) -> KernelResource:
    async with manager.operation(address(key), LeaseClient("client", "client"), policy()) as resource:
        return resource
