"""Shared pieces for the async-kernel spike probes.

The kernel is launched the way loommux launches it (private runtime root,
IPC transport, a server-authored KernelSpec), so what a probe observes is
what the daemon would observe after the migration.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
from collections import Counter
from pathlib import Path
from typing import Any

import anyio
import jupyter_core.utils as jcu
from jupyter_client.kernelspec import KernelSpec
from jupyter_client.manager import AsyncKernelManager

# run_sync looks both names up as module globals at call time, so patching the
# module counts every entry into either path: a fresh/cached loop on a thread
# without a running loop, or the helper-thread runner on one that has one.
SYNC_BRIDGE = Counter()
_ensure = jcu.ensure_event_loop
_run = jcu._TaskRunner.run


def _counted_ensure(*args: Any, **kwargs: Any):
    SYNC_BRIDGE["ensure_event_loop"] += 1
    return _ensure(*args, **kwargs)


def _counted_run(self, coro):
    SYNC_BRIDGE["task_runner"] += 1
    return _run(self, coro)


jcu.ensure_event_loop = _counted_ensure
jcu._TaskRunner.run = _counted_run


class SpikeKernelManager(AsyncKernelManager):
    def __init__(self, kernel_spec: KernelSpec, **kwargs: Any) -> None:
        self._spike_spec = kernel_spec
        super().__init__(**kwargs)

    @property
    def kernel_spec(self) -> KernelSpec:
        return self._spike_spec


def new_manager() -> tuple[SpikeKernelManager, Path]:
    root = Path(tempfile.mkdtemp(prefix="spike-kernel-"))
    connection_file = root / "kernel.json"
    spec = KernelSpec(
        argv=[sys.executable, "-m", "ipykernel_launcher", "-f", str(connection_file),
              "--HistoryManager.enabled=False", "--InteractiveShell.colors=nocolor"],
        display_name="spike", language="python", interrupt_mode="signal", resource_dir=str(root),
    )
    manager = SpikeKernelManager(spec, connection_file=str(connection_file), kernel_name="",
                                 transport="ipc", ip=str(root / "ipc"))
    return manager, root


def remove_root(root: Path) -> None:
    shutil.rmtree(root, ignore_errors=True)


def process_facts() -> dict[str, int]:
    fds = os.listdir("/proc/self/fd")
    kinds = Counter()
    for fd in fds:
        try:
            target = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            continue
        kinds["eventpoll" if "eventpoll" in target else "socket" if target.startswith("socket:") else "other"] += 1
    return {"fds": len(fds), **kinds, "threads": sorted(t.name for t in threading.enumerate())}


def children() -> list[int]:
    """Live child processes; zombies are reaped rather than counted."""
    path = Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children")
    live = []
    for pid in map(int, path.read_text().split()):
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except OSError:
            continue
        if state == "Z":
            os.waitpid(pid, os.WNOHANG)
        else:
            live.append(pid)
    return live


async def run_cell(client, code: str, *, limit: float = 60) -> dict[str, Any]:
    """Execute and collect this cell's iopub until idle, then its shell reply."""
    return await collect(client, client.execute(code), limit=limit)


async def collect(client, msg_id: str, *, limit: float = 60) -> dict[str, Any]:
    """Collect one submitted cell's iopub until idle, then its shell reply."""
    out: dict[str, Any] = {"stream": "", "result": None, "error": None, "status": None}
    with anyio.fail_after(limit):
        while True:
            msg = await client.get_iopub_msg()
            if msg["parent_header"].get("msg_id") != msg_id:
                continue
            kind, content = msg["msg_type"], msg["content"]
            if kind == "stream":
                out["stream"] += content["text"]
            elif kind == "execute_result":
                out["result"] = content["data"].get("text/plain")
            elif kind == "error":
                out["error"] = content["ename"]
            elif kind == "status" and content["execution_state"] == "idle":
                break
        while True:
            reply = await client.get_shell_msg()
            if reply["parent_header"].get("msg_id") == msg_id:
                out["status"] = reply["content"]["status"]
                out["pages"] = [p["data"].get("text/plain", "")[:60] for p in reply["content"].get("payload", []) if p.get("source") == "page"]
                return out
