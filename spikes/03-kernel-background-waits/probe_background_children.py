"""Does an interrupt of the kernel reach the agents a cell started in the background?

jupyter_client interrupts a POSIX kernel by signalling its process group, and
the Claude Agent SDK starts ``claude`` as a plain child; both are read from the
installed source below. This process stands in for a kernel: it must lead its
own process group, so run it under ``setsid``. It starts one plain child and
one through ``setsid``, ignores SIGINT itself, and signals its own group as
``LocalProvisioner.send_signal`` does.
Usage: setsid python probe_background_children.py
"""

import inspect
import os
import signal

import anyio
from claude_agent_sdk._internal.transport import subprocess_cli
from jupyter_client.provisioning.local_provisioner import LocalProvisioner


def facts() -> None:
    send_signal = inspect.getsource(LocalProvisioner.send_signal)
    print("LocalProvisioner.send_signal calls os.killpg on the kernel's group:", "os.killpg(self.pgid, signum)" in send_signal)
    connect = inspect.getsource(subprocess_cli)
    print("Agent SDK starts claude with anyio.open_process:", "anyio.open_process(" in connect,
          "· with a session or group of its own:", any(word in connect for word in ("start_new_session", "process_group", "setsid")))


async def main() -> None:
    assert os.getpgid(0) == os.getpid(), "run under setsid: this process must lead its group"
    plain = await anyio.open_process(["sleep", "30"])
    detached = await anyio.open_process(["setsid", "sleep", "30"])
    await anyio.sleep(0.3)
    group = os.getpgid(0)
    for name, process in (("plain", plain), ("setsid", detached)):
        print(f"{name:<7} pid {process.pid} pgid {os.getpgid(process.pid)} in the kernel's group: {os.getpgid(process.pid) == group}")
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    os.killpg(group, signal.SIGINT)
    await anyio.sleep(0.3)
    print("after killpg(SIGINT): plain returncode", plain.returncode, "· setsid returncode", detached.returncode)
    detached.terminate()
    await detached.wait()


facts()
anyio.run(main)
