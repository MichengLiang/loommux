import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest

from loommux.resource import (
    BindingKind,
    ClientLease,
    KernelNotFoundError,
    KernelRequest,
    KernelResourceManager,
    LeaseClient,
    LeaseMode,
    LeasePolicy,
    RequestMode,
    ResourceBusyError,
    ResourceLifecycle,
    ResourceNotFoundError,
    ResourceRoutingError,
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


def join(name: str) -> KernelRequest:
    return KernelRequest(name, RequestMode.JOIN)


def create(name: str) -> KernelRequest:
    return KernelRequest(name, RequestMode.ATTACH, create=True)


def attach(name: str) -> KernelRequest:
    return KernelRequest(name, RequestMode.ATTACH)


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


def lease(resource: KernelResource, client_id: str = "client") -> None:
    resource.client_leases[client_id] = ClientLease.create(LeaseClient(client_id, client_id), policy())


async def _first_call(manager: KernelResourceManager, client: LeaseClient, request: KernelRequest | None = None) -> KernelResource:
    return (await _admit(manager, client, request)).resource


async def _admit(manager: KernelResourceManager, client: LeaseClient, request: KernelRequest | None = None, lease_policy: LeasePolicy | None = None) -> Any:
    async with manager.operation(client, lease_policy or policy(), request=request) as admitted:
        return admitted


def test_attach_with_create_opens_a_missing_main_kernel_and_joins_an_existing_one(tmp_path: Path) -> None:
    async def scenario() -> None:
        FakeSession.identities = []
        manager = KernelResourceManager(tmp_path, "launch_cwd", session_factory=FakeSession)

        first = await _admit(manager, LeaseClient("a", "a"), create("task"))
        again = await _admit(manager, LeaseClient("a", "a"), create("task"))
        first.resource.session.executions = {1: object()}
        joined = await _admit(manager, LeaseClient("b", "b"), create("task"))

        assert (first.resource.name, first.binding.kind, first.binding.executions) == ("task", BindingKind.OPENED, 0)
        # Repeating the first-cell header changes nothing.
        assert again.resource is first.resource and again.binding is None
        # The name is the key: another session's connection neither numbers
        # the name nor keeps the second session out.
        assert joined.resource is first.resource
        assert joined.binding.as_dict() == {"name": "task", "kind": "attached", "executions": 1, "released": None}
        assert set(first.resource.client_leases) == {"a", "b"}
        assert [identity.name for identity in FakeSession.identities] == ["task"]
        await manager.stop()

    asyncio.run(scenario())


def test_two_sessions_creating_one_name_at_once_share_one_kernel(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(tmp_path, "launch_cwd", session_factory=FakeSession)

        admitted = await asyncio.gather(_admit(manager, LeaseClient("a", "a"), create("task")), _admit(manager, LeaseClient("b", "b"), create("task")))

        assert admitted[0].resource is admitted[1].resource
        assert sorted(a.binding.kind for a in admitted) == [BindingKind.ATTACHED, BindingKind.OPENED]
        assert len(await manager.list_resources()) == 1
        await manager.stop()

    asyncio.run(scenario())


def test_attaching_switches_at_any_time_and_lets_go_of_the_previous_main_kernel(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(tmp_path, "launch_cwd", session_factory=FakeSession)
        other = LeaseClient("other", "other")
        target = (await _admit(manager, other, create("target"))).resource
        client = LeaseClient("0123456789abcdef", "agent")
        accidental = (await _admit(manager, client)).resource
        child = await manager.create("build", accidental.resource_id)

        switched = await _admit(manager, client, attach("target"))
        again = await _admit(manager, client, attach(target.resource_id))
        ordinary = await _admit(manager, client)

        assert switched.resource is target
        assert (switched.binding.kind, switched.binding.released) == (BindingKind.ATTACHED, "private-01234567")
        # Attaching to the kernel the session already works in changes nothing.
        assert again.resource is target and again.binding is None
        assert ordinary.resource is target
        # Another session's connection does not stop a session from joining.
        assert set(target.client_leases) == {other.client_id, client.client_id}
        assert accidental.client_leases == {}
        assert await manager.sweep_once() == 2
        assert {accidental.lifecycle, child.lifecycle} == {ResourceLifecycle.STOPPED}
        await manager.stop()

    asyncio.run(scenario())


def test_attaching_to_a_missing_main_kernel_opens_nothing_and_lists_the_main_kernels(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(tmp_path, "launch_cwd", session_factory=FakeSession)
        connected = (await _admit(manager, LeaseClient("a", "a"), create("connected"))).resource
        waiting = await manager.get_or_create("waiting")
        child = await manager.create("build", connected.resource_id)

        with pytest.raises(KernelNotFoundError, match="no main kernel named 'gone'; main kernels: connected, waiting. Add --create to open it$"):
            await _admit(manager, LeaseClient("b", "b"), attach("gone"))
        # Child kernels belong to their home and are no attach target.
        with pytest.raises(KernelNotFoundError):
            await _admit(manager, LeaseClient("b", "b"), attach(child.resource_id))
        assert len(await manager.list_resources()) == 3
        assert waiting.client_leases == {}
        await manager.stop()

    asyncio.run(scenario())


def test_joining_by_connection_header_reports_whether_it_opened_the_kernel(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(tmp_path, "launch_cwd", session_factory=FakeSession)

        first = await _admit(manager, LeaseClient("a", "a"), join("lab"))
        second = await _admit(manager, LeaseClient("b", "b"), join("lab"))

        assert second.resource is first.resource
        assert [first.binding.kind, second.binding.kind] == [BindingKind.OPENED, BindingKind.ATTACHED]
        await manager.stop()

    asyncio.run(scenario())


def test_a_session_works_where_its_lease_is_and_its_request_counts_only_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(tmp_path, "launch_cwd", session_factory=FakeSession)
        client = LeaseClient("0123456789abcdef", "agent")

        first = await _first_call(manager, client)
        again = await _first_call(manager, client, join("elsewhere"))
        other = await _first_call(manager, LeaseClient("fedcba9876543210", "other"))

        assert (first.name, first.home, first.named) == ("private-01234567", None, False)
        assert again is first
        assert other is not first
        assert [resource.name for resource in await manager.list_resources()] == ["private-01234567", "private-fedcba98"]
        await manager.stop()

    asyncio.run(scenario())


def test_a_generated_name_another_session_holds_is_numbered(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(tmp_path, "launch_cwd", session_factory=FakeSession)

        first = await _first_call(manager, LeaseClient("abcdefgh-first", "a"))
        second = await _first_call(manager, LeaseClient("abcdefgh-second", "b"))

        assert (first.name, second.name) == ("private-abcdefgh", "private-abcdefgh-2")
        await manager.stop()

    asyncio.run(scenario())


def test_concurrent_first_calls_of_one_session_share_one_kernel(tmp_path: Path) -> None:
    async def scenario() -> None:
        FakeSession.created = 0
        manager = KernelResourceManager(tmp_path, "launch_cwd", session_factory=FakeSession)
        client = LeaseClient("0123456789abcdef", "agent")

        first, second = await asyncio.gather(_first_call(manager, client), _first_call(manager, client))

        assert first is second
        assert FakeSession.created == 1
        assert list(first.client_leases) == [client.client_id]
        await manager.stop()

    asyncio.run(scenario())


def test_a_session_bound_by_a_concurrent_call_leaves_the_other_kernel_unleased(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(tmp_path, "launch_cwd", session_factory=FakeSession)
        client = LeaseClient("0123456789abcdef", "agent")

        first, second = await asyncio.gather(_first_call(manager, client, join("lab")), _first_call(manager, client))

        assert first is second
        [unleased] = [resource for resource in await manager.list_resources() if resource is not first]
        assert unleased.client_leases == {}
        assert await manager.sweep_once() == 1
        await manager.stop()

    asyncio.run(scenario())


def test_child_kernels_live_in_one_home_and_leave_with_its_main_kernel(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(tmp_path, "launch_cwd", session_factory=FakeSession)
        root = await manager.get_or_create("agent", named=False)
        lease(root)

        x = await manager.create("x", root.resource_id)
        # A child kernel asking for a kernel opens it in the same home.
        y = await manager.create("y", x.resource_id)
        assert (x.home, y.home) == (root.resource_id, root.resource_id)
        assert await manager.create("y", x.resource_id) is y
        assert await manager.lookup(x.resource_id, y.resource_id) is x
        assert await manager.lookup("agent", y.resource_id) is root
        assert await manager.lookup("y", y.resource_id) is y
        snapshot = await manager.snapshot_one(x.resource_id)
        assert (snapshot["scope"], snapshot["home"], snapshot["named"]) == ("child", "agent", True)
        assert (await manager.snapshot_one(root.resource_id))["scope"] == "main"
        assert [resource.name for resource in await manager.list_resources()] == ["agent", "x", "y"]

        assert await manager.sweep_once() == 0
        assert {x.lifecycle, y.lifecycle} == {ResourceLifecycle.RUNNING}
        del root.client_leases["client"]
        assert await manager.sweep_once() == 3
        assert {root.lifecycle, x.lifecycle, y.lifecycle} == {ResourceLifecycle.STOPPED}
        await manager.stop()

    asyncio.run(scenario())


def test_two_homes_each_have_their_own_child_kernel_of_one_name(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(tmp_path, "launch_cwd", session_factory=FakeSession)
        first = await manager.get_or_create("first")
        second = await manager.get_or_create("second")
        lease(first, "a")
        lease(second, "b")

        build_first = await manager.create("build", first.resource_id)
        build_second = await manager.create("build", second.resource_id)

        assert build_first is not build_second
        # A bare name is read from the asking kernel's main kernel; a path from the root.
        assert await manager.lookup("build", second.resource_id) is build_second
        assert await manager.lookup("build", build_first.resource_id) is build_first
        assert await manager.lookup("/first/build", None) is build_first
        assert await manager.lookup("second/build", build_first.resource_id) is build_second
        # The listing is every kernel; each child kernel's row names its home.
        assert [item["home"] for item in await manager.snapshot()] == [None, None, "first", "second"]
        await manager.stop()

    asyncio.run(scenario())


def test_lookup_reads_paths_and_opens_nothing_and_create_opens_only_child_kernels(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(tmp_path, "launch_cwd", session_factory=FakeSession)
        agent = await manager.get_or_create("agent")
        lab = await manager.create("lab", agent.resource_id)

        # Without a context a bare name is a main kernel's.
        with pytest.raises(KernelNotFoundError, match="no kernel 'lab'; main kernels: /agent$"):
            await manager.lookup("lab", None)
        with pytest.raises(KernelNotFoundError, match="no kernel 'gone'; kernels of /agent: agent, lab$"):
            await manager.lookup("gone", lab.resource_id)
        with pytest.raises(KernelNotFoundError, match="no kernel 'nobody'; main kernels: /agent$"):
            await manager.lookup("/nobody/lab", None)
        with pytest.raises(ResourceRoutingError, match="not a kernel path"):
            await manager.lookup("a/b/c", None)
        with pytest.raises(ResourceNotFoundError, match="asking kernel was not found"):
            await manager.create("lab", "retired-holder")
        with pytest.raises(ResourceRoutingError, match="kernel name must contain printable characters only"):
            await manager.create("a\tb", agent.resource_id)
        # A main kernel lives by a session's lease, so only a session opens one.
        with pytest.raises(ResourceRoutingError, match="only an MCP session opens"):
            await manager.create("/fresh", None)
        with pytest.raises(ResourceRoutingError, match="only an MCP session opens"):
            await manager.create("fresh", None)
        with pytest.raises(ResourceRoutingError, match="name of its main kernel"):
            await manager.create("agent/agent", None)
        assert await manager.lookup("agent", None) is agent
        assert await manager.lookup("/agent", lab.resource_id) is agent
        assert await manager.lookup(lab.resource_id, None) is lab
        assert await manager.lookup("/agent/lab", None) is lab
        assert await manager.create("agent/lab", None) is lab
        assert len(await manager.list_resources()) == 2
        # From outside any kernel a path names the main kernel to open in.
        outside = await manager.create("/agent/outside", None)
        assert outside.home == agent.resource_id
        # Two callers creating one name at once get one kernel.
        first, second = await asyncio.gather(manager.create("twin", agent.resource_id), manager.create("twin", lab.resource_id))
        assert first is second
        await manager.stop()

    asyncio.run(scenario())


def test_a_child_kernel_is_retired_with_its_main_kernel(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(tmp_path, "launch_cwd", session_factory=FakeSession)
        agent = await manager.get_or_create("agent")
        lease(agent)
        lab = await manager.create("lab", agent.resource_id)

        await manager.recycle(agent.resource_id)

        assert await manager.sweep_once() == 1
        assert lab.lifecycle is ResourceLifecycle.STOPPED
        await manager.stop()

    asyncio.run(scenario())


def test_a_session_returning_to_an_orphaned_main_kernel_returns_it_to_running(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(tmp_path, "launch_cwd", session_factory=FakeSession, orphan_grace_seconds=60)
        lab = await manager.get_or_create("lab")
        lab.session.current_execution = 1

        assert await manager.sweep_once() == 0
        assert lab.lifecycle is ResourceLifecycle.ORPHANED
        assert await _first_call(manager, LeaseClient("client", "client"), join("lab")) is lab
        assert lab.lifecycle is ResourceLifecycle.RUNNING
        assert lab.orphaned_at is None
        await manager.stop()

    asyncio.run(scenario())


def test_a_kernel_opened_while_its_home_retires_is_not_handed_out(tmp_path: Path) -> None:
    class SlowSession(FakeSession):
        release = threading.Event()

        def start_workspace(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            result = super().start_workspace(*args, **kwargs)
            if kwargs["identity"].name == "lab":
                assert type(self).release.wait(timeout=2)
            return result

    async def scenario() -> None:
        manager = KernelResourceManager(tmp_path, "launch_cwd", session_factory=SlowSession)
        agent = await manager.get_or_create("agent")
        opening = asyncio.create_task(manager.create("lab", agent.resource_id))
        await asyncio.sleep(0.05)
        await manager.recycle(agent.resource_id)
        SlowSession.release.set()

        with pytest.raises(ResourceNotFoundError, match="was retired while 'lab' started"):
            await opening
        await manager.stop()

    asyncio.run(scenario())


def test_renaming_numbers_a_taken_name_within_the_kernels_scope(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(tmp_path, "launch_cwd", session_factory=FakeSession)
        generated = await _first_call(manager, LeaseClient("0123456789abcdef", "agent"))
        report = await manager.get_or_create("report")
        other = await manager.get_or_create("other")
        mine = await manager.create("build", generated.resource_id)
        theirs = await manager.create("build", other.resource_id)
        sibling = await manager.create("lab", generated.resource_id)

        assert (await manager.rename(generated.resource_id, "report")).name == "report-2"
        # A renamed generated kernel counts as named, and is found by its new name.
        assert generated.named is True
        assert await manager.get_or_create("report-2") is generated
        assert await manager.lookup("report-2", None) is generated
        # Child kernel names are unique inside their home only.
        assert (await manager.rename(theirs.resource_id, "tests")).name == "tests"
        assert (await manager.rename(mine.resource_id, "tests")).name == "tests"
        assert (await manager.rename(sibling.resource_id, "tests")).name == "tests-2"
        assert await manager.lookup("tests", generated.resource_id) is mine
        assert (await manager.rename(report.resource_id, "report")).name == "report"
        # Inside a home the main kernel resolves first, so neither may take
        # the other's name: it would hide that kernel from every lookup.
        assert (await manager.rename(sibling.resource_id, "report-2")).name == "report-2-2"
        assert (await manager.rename(generated.resource_id, "tests")).name == "tests-2"
        with pytest.raises(ResourceRoutingError, match="kernel name must not exceed 256 characters"):
            await manager.rename(report.resource_id, "x" * 257)
        with pytest.raises(ResourceRoutingError, match="must not contain '/'"):
            await manager.rename(report.resource_id, "a/b")
        with pytest.raises(ResourceNotFoundError):
            await manager.rename("missing", "anything")
        await manager.stop()

    asyncio.run(scenario())


def test_program_operations_count_as_work_without_creating_a_lease(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = KernelResourceManager(tmp_path, "launch_cwd", session_factory=FakeSession)
        lab = await manager.get_or_create("lab")

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
            manager.get_or_create("same"),
            manager.get_or_create("same"),
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
        resource = await manager.get_or_create("lab")
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
                    manager.get_or_create("first"),
                    manager.get_or_create("second"),
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
        resource = await manager.get_or_create("busy")
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
        resource = await manager.get_or_create("recover")
        resource_id = resource.resource_id
        previous_pid = resource.kernel_pid
        resource.session.kernel = None

        async with manager.operation(LeaseClient("client", "client"), policy(), request=join("recover")) as admitted:
            recovered = admitted.resource
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
        first = await manager.get_or_create("first-control")
        second = await manager.get_or_create("second-control")

        interrupted = await manager.interrupt(first.resource_id)
        restarted = await manager.restart(first.resource_id)
        health = await manager.health(first.resource_id)

        assert interrupted["status"] == "idle"
        assert restarted["status"] == "restarted"
        assert health["ok"] is True
        assert health["kernel_pid"] == first.kernel_pid
        assert await manager.recycle_idle() == 2
        assert await manager.snapshot() == []

        busy = await manager.get_or_create("busy-control")
        idle = await manager.get_or_create("idle-control")
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
        resource = await manager.get_or_create("serialized")
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
        resource = await manager.get_or_create("retired")
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
        resource = await manager.get_or_create("dead")
        resource.session.kernel = None

        [snapshot] = await manager.snapshot()
        health = await manager.health(resource.resource_id)

        assert snapshot["kernel_pid"] is None
        assert snapshot["kernel_state"] == "stopped"
        assert snapshot["lifecycle"] == "running"
        assert health["ok"] is False
        assert resource.lifecycle is ResourceLifecycle.RUNNING
        async with manager.operation(LeaseClient("client", "client"), policy(), request=join("dead")) as admitted:
            recovered = admitted.resource
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
            async with manager.operation(client, policy(), request=join("gap")) as admitted:
                resource = admitted.resource
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
        async with manager.operation(client, policy(), request=join("gap")) as admitted:
            resource = admitted.resource
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
            async with manager.operation(client, policy(), request=join("replace"), replaces_kernel=True) as admitted:
                resource = admitted.resource
                await asyncio.to_thread(resource.session.restart)

        async def other_tool() -> int | None:
            async with manager.operation(client, policy(), request=join("replace")) as admitted:
                resource = admitted.resource
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
        retired = await manager.get_or_create("retired")
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
    async with manager.operation(LeaseClient("client", "client"), policy(), request=join(key)) as admitted:
        return admitted.resource
