"""Does a kernel still exit when its parent dies after the parent-handle duplicate was closed early?

ipykernel's ParentPollerWindows watches its *inherited* copy of the handle,
so closing the launcher's copy right after spawn must not change this. The
parent below starts a kernel, closes its own copy (or not), prints the kernel
PID, and dies with os._exit; the outer process then watches the kernel.

    uv run --no-project --with jupyter_client --with ipykernel --with psutil python issue_windows_parent_death.py
"""

import subprocess
import sys
import time

import psutil

PARENT = r"""
import _winapi, os, sys
from jupyter_client.manager import KernelManager
dup = _winapi.DuplicateHandle
mine = []
def rec(*a):
    h = dup(*a)
    if sys._getframe(1).f_code.co_filename.endswith("launcher.py"):
        mine.append(h)
    return h
_winapi.DuplicateHandle = rec
km = KernelManager(kernel_name="python3")
km.start_kernel()
kc = km.blocking_client(); kc.start_channels(); kc.wait_for_ready(timeout=60); kc.stop_channels()
if sys.argv[1] == "early-close":
    _winapi.CloseHandle(mine.pop())
print(km.provisioner.process.pid, flush=True)
os._exit(0)
"""

for mode in ("keep", "early-close"):
    parent = subprocess.run([sys.executable, "-c", PARENT, mode], capture_output=True, text=True, timeout=120)
    kernel_pid = int(parent.stdout.split()[-1])
    tree = [kernel_pid, *(c.pid for c in psutil.Process(kernel_pid).children(recursive=True))] if psutil.pid_exists(kernel_pid) else [kernel_pid]
    started = time.monotonic()
    while any(psutil.pid_exists(p) for p in tree) and time.monotonic() - started < 30:
        time.sleep(0.2)
    gone = not any(psutil.pid_exists(p) for p in tree)
    print(f"{mode}: kernel exited after parent death: {gone} ({time.monotonic() - started:.1f} s)")
    for p in tree:
        if psutil.pid_exists(p):
            psutil.Process(p).kill()
