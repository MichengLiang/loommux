"""Each kernel launched on Windows leaves two handles open in the launching process.

Repro for the jupyter_client issue filed from this spike. Requires Windows:

    uv run --no-project --with jupyter_client --with ipykernel --with psutil python issue_windows_handles.py MODE

MODE is ``as-is`` (unchanged), ``close-after`` (close both launcher handles
after shutdown), or ``fix`` (close the parent-handle duplicate as soon as the
kernel is spawned, the interrupt event after shutdown).
"""

import _winapi
import gc
import sys

import jupyter_client.win_interrupt as win_interrupt
import psutil
from jupyter_client.manager import KernelManager

MODE = sys.argv[1]
events, duplicates = [], []
create_event, duplicate = win_interrupt.create_interrupt_event, _winapi.DuplicateHandle


def recorded_event():
    events.append(create_event())
    return events[-1]


def recorded_duplicate(*args):
    handle = duplicate(*args)
    if sys._getframe(1).f_code.co_filename.endswith("launcher.py"):  # not subprocess's own
        duplicates.append(handle)
    return handle


if MODE != "as-is":
    win_interrupt.create_interrupt_event = recorded_event
    _winapi.DuplicateHandle = recorded_duplicate


def one_kernel():
    km = KernelManager(kernel_name="python3")
    km.start_kernel()
    if MODE == "fix":
        # The kernel inherited its own copy at spawn; the launching process never uses this one.
        _winapi.CloseHandle(duplicates.pop())
    kc = km.blocking_client()
    kc.start_channels()
    kc.wait_for_ready(timeout=60)
    reply = kc.execute_interactive("1 + 1", timeout=60, output_hook=lambda msg: None)
    assert reply["content"]["status"] == "ok"
    kc.stop_channels()
    km.shutdown_kernel(now=True)
    if MODE != "as-is":
        for handle in events + duplicates:
            _winapi.CloseHandle(handle)
        events.clear()
        duplicates.clear()


me = psutil.Process()
print(f"{MODE}: handles before: {me.num_handles()}")
for round_ in range(1, 4):
    for _ in range(20):
        one_kernel()
    gc.collect()
    print(f"{MODE}: after {20 * round_} kernels: {me.num_handles()}")
