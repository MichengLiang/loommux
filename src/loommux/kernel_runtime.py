"""Own the private IPython process runtime behind Jupyter's cross-platform API."""

from __future__ import annotations

import contextlib
import shutil
import sys
from importlib import import_module
from pathlib import Path
from typing import Any

from jupyter_client.blocking.client import BlockingKernelClient
from jupyter_client.kernelspec import KernelSpec
from jupyter_client.manager import KernelManager

from loommux.coding_agent_kernel import KernelLaunch


class _LoommuxKernelManager(KernelManager):
    """A manager with the server-authored kernel specification only.

    The normal KernelManager lookup would allow a user-installed ``python3``
    kernelspec to replace the command and provisioner loommux has authorized.
    This manager gives Jupyter its required KernelSpec while keeping that
    selection entirely inside the private runtime root.
    """

    def __init__(self, kernel_spec: KernelSpec, **kwargs: object) -> None:
        self._loommux_kernel_spec = kernel_spec
        super().__init__(**kwargs)

    @property
    def kernel_spec(self) -> KernelSpec:
        return self._loommux_kernel_spec


class _KernelContainment:
    """Contain kernel descendants whose platform lacks Unix process groups."""

    def attach(self, _pid: int) -> None:
        pass

    def close(self) -> None:
        pass


class _WindowsKernelContainment(_KernelContainment):
    """Use a Job Object so reset and close also end kernel child processes."""

    def __init__(self) -> None:
        self._win32api: Any = import_module("win32api")
        self._win32con: Any = import_module("win32con")
        self._win32job: Any = import_module("win32job")
        self._job: Any = None

    def attach(self, pid: int) -> None:
        job: Any = self._win32job.CreateJobObject(None, "")
        information = self._win32job.QueryInformationJobObject(job, self._win32job.JobObjectExtendedLimitInformation)
        information["BasicLimitInformation"]["LimitFlags"] |= self._win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        self._win32job.SetInformationJobObject(job, self._win32job.JobObjectExtendedLimitInformation, information)
        process: Any = self._win32api.OpenProcess(self._win32con.PROCESS_SET_QUOTA | self._win32con.PROCESS_TERMINATE, False, pid)
        try:
            self._win32job.AssignProcessToJobObject(job, process)
        except Exception:
            job.Close()
            raise
        finally:
            process.Close()
        self._job = job

    def close(self) -> None:
        job, self._job = self._job, None
        if job is not None:
            job.Close()


class KernelRuntime:
    """Start, interrupt, and stop one private kernel without OS-specific calls."""

    def __init__(self, workspace: Path, python_path: Path) -> None:
        self.workspace = workspace
        self.python_path = python_path
        self.launch: KernelLaunch | None = None
        self.manager: _LoommuxKernelManager | None = None
        self.client: BlockingKernelClient | None = None
        self._containment: _KernelContainment | None = None
        self._pid: int | None = None

    @property
    def pid(self) -> int | None:
        return self._pid

    def start(self, timeout_seconds: float) -> BlockingKernelClient:
        launch = KernelLaunch.create(self.python_path, self.workspace)
        manager = _LoommuxKernelManager(_kernel_spec(launch), connection_file=str(launch.connection_file), kernel_name="")
        containment: _KernelContainment | None = None
        client: BlockingKernelClient | None = None
        try:
            manager.start_kernel(cwd=str(self.workspace), env=launch.environment)
            pid = _kernel_pid(manager)
            containment = _create_containment()
            containment.attach(pid)
            started_client = manager.client()
            client = started_client
            started_client.start_channels()
            started_client.wait_for_ready(timeout=timeout_seconds)
        except Exception:
            if client is not None:
                with contextlib.suppress(Exception):
                    client.stop_channels()
            if containment is not None:
                with contextlib.suppress(Exception):
                    containment.close()
            with contextlib.suppress(Exception):
                manager.shutdown_kernel(now=True)
            _remove_launch_root(launch)
            raise
        self.launch = launch
        self.manager = manager
        self.client = started_client
        self._containment = containment
        self._pid = pid
        return started_client

    def interrupt(self) -> None:
        manager = self.manager
        if manager is None or not manager.is_alive():
            return
        manager.interrupt_kernel()

    def is_alive(self) -> bool:
        manager = self.manager
        return manager is not None and manager.is_alive()

    def shutdown(self) -> None:
        manager, self.manager = self.manager, None
        client, self.client = self.client, None
        containment, self._containment = self._containment, None
        launch, self.launch = self.launch, None
        self._pid = None
        try:
            if manager is not None:
                with contextlib.suppress(Exception):
                    manager.shutdown_kernel(now=True)
        finally:
            if client is not None:
                with contextlib.suppress(Exception):
                    client.stop_channels()
            if containment is not None:
                with contextlib.suppress(Exception):
                    containment.close()
            if launch is not None:
                _remove_launch_root(launch)


def _kernel_spec(launch: KernelLaunch) -> KernelSpec:
    return KernelSpec(
        argv=list(launch.command),
        display_name="loommux private IPython kernel",
        language="python",
        interrupt_mode="signal",
        resource_dir=str(launch.runtime_root),
    )


def _kernel_pid(manager: KernelManager) -> int:
    provisioner = manager.provisioner
    pid = getattr(provisioner, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        raise RuntimeError("kernel manager did not expose a process identifier")
    return pid


def _create_containment() -> _KernelContainment:
    if sys.platform == "win32":
        return _WindowsKernelContainment()
    return _KernelContainment()


def _remove_launch_root(launch: KernelLaunch) -> None:
    launch.connection_file.unlink(missing_ok=True)
    shutil.rmtree(launch.runtime_root, ignore_errors=True)
