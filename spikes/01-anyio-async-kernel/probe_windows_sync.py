"""Probe 4b: the control for probe 4's Windows handle growth.

The same churn (20 kernels per round, three rounds, a full collection
before each reading) with the synchronous KernelManager and
BlockingKernelClient the daemon uses before the migration. Growth here as
well means the async migration does not introduce it.

The mode argument: ``default`` changes nothing; ``destroy`` destroys the
manager's and the client's zmq contexts after shutdown; ``close`` records the
two handles jupyter_client.launcher creates for every Windows kernel (the
interrupt event and the inheritable duplicate of the parent process handle,
which nothing in jupyter_client closes) and closes them after shutdown.

    uv run --no-project --with jupyter_client --with ipykernel --with psutil python probe_windows_sync.py MODE
"""

from __future__ import annotations

import _winapi
import gc
import json
import queue
import shutil
import sys
import tempfile
from pathlib import Path

import jupyter_client.win_interrupt as win_interrupt
import psutil
from jupyter_client.kernelspec import KernelSpec
from jupyter_client.manager import KernelManager

ME = psutil.Process()
MODE = sys.argv[1] if len(sys.argv) > 1 else "default"
LAUNCH_HANDLES: list[int] = []

if MODE == "close":
    # launch_kernel imports both names inside the function, so patching the
    # module attributes intercepts every launch.
    _create_event, _duplicate = win_interrupt.create_interrupt_event, _winapi.DuplicateHandle

    def _recorded_event():
        handle = _create_event()
        LAUNCH_HANDLES.append(int(handle))
        return handle

    def _recorded_duplicate(*args):
        handle = _duplicate(*args)
        # subprocess duplicates its standard-stream handles through the same
        # function and closes them itself; only the launcher's copy is ours.
        if sys._getframe(1).f_code.co_filename.endswith("launcher.py"):
            LAUNCH_HANDLES.append(int(handle))
        return handle

    win_interrupt.create_interrupt_event = _recorded_event
    _winapi.DuplicateHandle = _recorded_duplicate


class SpikeKernelManager(KernelManager):
    def __init__(self, spec: KernelSpec, **kwargs) -> None:
        self._spec = spec
        super().__init__(**kwargs)

    @property
    def kernel_spec(self) -> KernelSpec:
        return self._spec


def one(i: int) -> None:
    root = Path(tempfile.mkdtemp(prefix="spike-kernel-"))
    cf = root / "kernel.json"
    spec = KernelSpec(argv=[sys.executable, "-m", "ipykernel_launcher", "-f", str(cf), "--HistoryManager.enabled=False"],
                      display_name="spike", language="python", interrupt_mode="signal", resource_dir=str(root))
    manager = SpikeKernelManager(spec, connection_file=str(cf), kernel_name="")
    manager.start_kernel(cwd=str(root))
    client = manager.blocking_client()
    client.start_channels(hb=False)
    try:
        client.wait_for_ready(timeout=60)
        msg_id = client.execute(f"print({i})")
        while True:
            try:
                msg = client.get_iopub_msg(timeout=30)
            except queue.Empty:
                raise RuntimeError("no idle") from None
            if msg["parent_header"].get("msg_id") == msg_id and msg["msg_type"] == "status" \
                    and msg["content"]["execution_state"] == "idle":
                break
    finally:
        client.stop_channels()
        manager.shutdown_kernel(now=True)
        if MODE == "destroy":
            client.context.destroy(linger=0)
            manager.context.destroy(linger=0)
        if MODE == "close":
            while LAUNCH_HANDLES:
                _winapi.CloseHandle(LAUNCH_HANDLES.pop())
        shutil.rmtree(root, ignore_errors=True)


report = {"mode": MODE, "before": ME.num_handles(), "rounds": []}
for _ in range(3):
    for i in range(20):
        one(i)
    gc.collect()
    report["rounds"].append({"handles": ME.num_handles(), "children": len(ME.children(recursive=True))})
print(json.dumps(report))
