"""Probe 4: the same async lifecycle on Windows, run from an ephemeral uv env.

Windows differs where it matters: asyncio's default Proactor loop has no
add_reader, which zmq.asyncio needs; ipykernel's interrupt arrives through
an inherited event handle rather than SIGINT; and there are no /proc facts,
so psutil counts handles, threads, and children.

    uv run --no-project --with jupyter_client --with ipykernel --with anyio --with psutil python probe_windows.py
"""

from __future__ import annotations

import asyncio
import gc
import json
import platform
import shutil
import sys
import tempfile
import threading
import time
import warnings
from pathlib import Path

import anyio
import jupyter_core.utils as jcu
import psutil
from jupyter_client.kernelspec import KernelSpec
from jupyter_client.manager import AsyncKernelManager

SYNC_BRIDGE = {"calls": 0}
_ensure, _run = jcu.ensure_event_loop, jcu._TaskRunner.run
jcu.ensure_event_loop = lambda *a, **k: (SYNC_BRIDGE.__setitem__("calls", SYNC_BRIDGE["calls"] + 1), _ensure(*a, **k))[1]
jcu._TaskRunner.run = lambda self, coro: (SYNC_BRIDGE.__setitem__("calls", SYNC_BRIDGE["calls"] + 1), _run(self, coro))[1]
ME = psutil.Process()


class SpikeKernelManager(AsyncKernelManager):
    def __init__(self, spec: KernelSpec, **kwargs) -> None:
        self._spec = spec
        super().__init__(**kwargs)

    @property
    def kernel_spec(self) -> KernelSpec:
        return self._spec


def new_manager() -> tuple[SpikeKernelManager, Path]:
    root = Path(tempfile.mkdtemp(prefix="spike-kernel-"))
    cf = root / "kernel.json"
    spec = KernelSpec(argv=[sys.executable, "-m", "ipykernel_launcher", "-f", str(cf),
                            "--HistoryManager.enabled=False"],
                      display_name="spike", language="python", interrupt_mode="signal", resource_dir=str(root))
    return SpikeKernelManager(spec, connection_file=str(cf), kernel_name=""), root


def facts() -> dict:
    return {"handles": ME.num_handles(), "threads": sorted(t.name for t in threading.enumerate()),
            "children": len(ME.children(recursive=True))}


async def collect(client, msg_id: str, limit: float = 60) -> dict:
    out = {"stream": "", "error": None}
    with anyio.fail_after(limit):
        while True:
            msg = await client.get_iopub_msg()
            if msg["parent_header"].get("msg_id") != msg_id:
                continue
            if msg["msg_type"] == "stream":
                out["stream"] += msg["content"]["text"]
            elif msg["msg_type"] == "error":
                out["error"] = msg["content"]["ename"]
            elif msg["msg_type"] == "status" and msg["content"]["execution_state"] == "idle":
                return out


async def started():
    manager, root = new_manager()
    await manager.start_kernel(cwd=str(root))
    client = manager.client()
    client.start_channels(hb=False)
    await client.wait_for_ready(timeout=60)
    return manager, client, root


async def stop(manager, client, root) -> None:
    client.stop_channels()
    with anyio.CancelScope(shield=True):
        await manager.shutdown_kernel(now=True)
    shutil.rmtree(root, ignore_errors=True)


async def main() -> dict:
    report = {"platform": platform.platform(), "python": sys.version.split()[0],
              "loop": type(asyncio.get_running_loop()).__name__, "before": facts()}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        manager, client, root = await started()
        report["running"] = facts()
        report["execute"] = await collect(client, client.execute("print('hi from windows')"))

        t = time.perf_counter()
        msg = client.execute("import time; time.sleep(20)")
        await anyio.sleep(1)
        await manager.interrupt_kernel()
        with anyio.move_on_after(10) as scope:
            report["interrupt"] = await collect(client, msg)
        report["interrupt_timed_out"] = scope.cancelled_caught
        report["interrupt_seconds"] = round(time.perf_counter() - t, 2)
        await stop(manager, client, root)
        report["warnings"] = sorted({f"{w.category.__name__}: {str(w.message)[:120]}" for w in caught})

    # Steady state: one round is 20 kernels churned 4 at a time plus 5
    # cancelled starts. A leak grows round after round; the one-time cost of
    # pyzmq's selector thread does not.
    limiter = anyio.CapacityLimiter(4)

    async def one(i: int) -> None:
        async with limiter:
            m, c, r = await started()
            try:
                assert (await collect(c, c.execute(f"print({i})")))["stream"] == f"{i}\n"
            finally:
                await stop(m, c, r)

    async def cancelled_start() -> None:
        manager, root = new_manager()
        with anyio.move_on_after(0.05):
            try:
                await manager.start_kernel(cwd=str(root))
                await anyio.sleep(5)
            finally:
                with anyio.CancelScope(shield=True):
                    if manager.has_kernel:
                        await manager.shutdown_kernel(now=True)
        shutil.rmtree(root, ignore_errors=True)

    async def churn() -> None:
        async with anyio.create_task_group() as tg:
            for i in range(20):
                tg.start_soon(one, i)

    async def cancels() -> None:
        for _ in range(5):
            await cancelled_start()

    # Each phase runs alone so that growth can be attributed; the handle count
    # is read before and after a full collection to separate a true leak from
    # handles that only a full garbage collection would release.
    for name, phase in (("churn20", churn), ("cancel5", cancels), ("churn20_again", churn)):
        rounds = []
        for _ in range(3):
            await phase()
            await anyio.sleep(0.5)
            before_gc = ME.num_handles()
            gc.collect()
            rounds.append({"handles": before_gc, "after_gc": ME.num_handles(), "children": len(ME.children(recursive=True))})
        report[name] = rounds
    report["sync_bridge_calls"] = SYNC_BRIDGE["calls"]
    return report


async def guarded() -> dict:
    with anyio.fail_after(240):
        return await main()


print(json.dumps(anyio.run(guarded), indent=1))
