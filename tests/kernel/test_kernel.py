from __future__ import annotations

import asyncio
import gc
import json
import shutil
import sys
import time
from pathlib import Path

import pytest

from loommux.kernel.launch import _IPYTHON_PROMPT_STRIPPING_REMOVAL, KernelLaunch
from loommux.kernel.runtime import KernelRuntime, _kernel_spec, _KernelContainment
from loommux.session import IPythonSession


def test_kernel_launch_builds_the_required_command_and_controlled_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("IPYTHONDIR", str(home / "inherited-ipython"))
    monkeypatch.setenv("JUPYTER_CONFIG_DIR", str(home / "inherited-jupyter"))
    monkeypatch.setenv("CLICOLOR", "1")
    monkeypatch.setenv("CLICOLOR_FORCE", "1")
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("LOOMMUX_TEST_PRESERVED", "preserved")

    launch = KernelLaunch.create(Path(sys.executable).absolute(), workspace)
    try:
        assert launch.command == (
            str(Path(sys.executable).absolute()),
            "-m",
            "ipykernel_launcher",
            "-f",
            str(launch.connection_file),
            f"--ipython-dir={launch.ipython_dir}",
            "--InteractiveShell.colors=nocolor",
            "--InteractiveShell.cache_size=0",
            "--HistoryManager.enabled=False",
            "--InteractiveShellApp.exec_PYTHONSTARTUP=False",
            (
                "--InteractiveShellApp.exec_lines="
                "import os; os.environ.pop('CLICOLOR', None); "
                "os.environ.pop('CLICOLOR_FORCE', None); "
                "os.environ.pop('FORCE_COLOR', None)"
            ),
            f"--InteractiveShellApp.exec_lines={_IPYTHON_PROMPT_STRIPPING_REMOVAL}",
        )
        assert launch.ipython_dir.is_dir()
        assert launch.jupyter_config_dir.is_dir()
        assert launch.environment["IPYTHONDIR"] == str(launch.ipython_dir)
        assert launch.environment["JUPYTER_CONFIG_DIR"] == str(launch.jupyter_config_dir)
        assert launch.environment["LOOMMUX_TEST_PRESERVED"] == "preserved"
        for name in ("CLICOLOR", "CLICOLOR_FORCE", "FORCE_COLOR"):
            assert name not in launch.environment
        assert {name: launch.environment[name] for name in ("NO_COLOR", "PY_COLORS")} == {
            "NO_COLOR": "1",
            "PY_COLORS": "0",
        }
        assert not _is_within(launch.runtime_root, workspace)
    finally:
        shutil.rmtree(launch.runtime_root, ignore_errors=True)


def test_kernel_runtime_start_failure_cleans_its_private_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    launches: list[KernelLaunch] = []
    original_create = KernelLaunch.create

    def record_launch(python_path: Path, launch_workspace: Path) -> KernelLaunch:
        launch = original_create(python_path, launch_workspace)
        launches.append(launch)
        return launch

    def fail_start_kernel(*_args: object, **_kwargs: object) -> None:
        raise OSError("kernel process did not start")

    monkeypatch.setattr("loommux.kernel.runtime.KernelLaunch.create", record_launch)
    monkeypatch.setattr("loommux.kernel.runtime._LoommuxKernelManager.start_kernel", fail_start_kernel)
    runtime = KernelRuntime(workspace, Path(sys.executable).absolute())

    with pytest.raises(OSError, match="kernel process did not start"):
        runtime.start(10)

    assert len(launches) == 1
    assert not launches[0].runtime_root.exists()
    assert runtime.launch is None
    runtime.shutdown()


def test_kernel_runtime_closes_channels_when_readiness_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    class FailingClient:
        channels_started = False
        channels_stopped = False

        def start_channels(self) -> None:
            self.channels_started = True

        def wait_for_ready(self, *, timeout: float) -> None:
            assert timeout == 10
            raise RuntimeError("kernel readiness failed")

        def stop_channels(self) -> None:
            self.channels_stopped = True

    class FailingManager:
        client_instance = FailingClient()

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.provisioner = type("Provisioner", (), {"pid": 123})()

        def start_kernel(self, **_kwargs: object) -> None:
            pass

        def client(self) -> FailingClient:
            return self.client_instance

        def shutdown_kernel(self, *, now: bool) -> None:
            assert now is True

    monkeypatch.setattr("loommux.kernel.runtime._LoommuxKernelManager", FailingManager)
    monkeypatch.setattr("loommux.kernel.runtime._create_containment", _KernelContainment)
    runtime = KernelRuntime(workspace, Path(sys.executable).absolute())

    with pytest.raises(RuntimeError, match="kernel readiness failed"):
        runtime.start(10)

    assert FailingManager.client_instance.channels_started is True
    assert FailingManager.client_instance.channels_stopped is True
    assert runtime.launch is None


def test_kernel_spec_uses_signal_interrupts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    launch = KernelLaunch.create(Path(sys.executable).absolute(), workspace)
    try:
        assert _kernel_spec(launch).interrupt_mode == "signal"
    finally:
        shutil.rmtree(launch.runtime_root, ignore_errors=True)




def test_kernel_launch_allows_the_platform_temp_directory_inside_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    temp_directory = home / "Temp"
    workspace.mkdir()
    temp_directory.mkdir(parents=True)
    monkeypatch.setattr("loommux.kernel.launch.tempfile.gettempdir", lambda: str(temp_directory))

    launch = KernelLaunch.create(Path(sys.executable).absolute(), workspace)
    try:
        assert _is_within(launch.runtime_root, home)
        assert not _is_within(launch.runtime_root, workspace)
    finally:
        shutil.rmtree(launch.runtime_root, ignore_errors=True)


def test_kernel_ignores_hostile_user_state_and_reset_replaces_its_private_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    user_ipython = home / ".ipython"
    user_jupyter = home / ".jupyter"
    profile_startup = user_ipython / "profile_default" / "startup" / "00-hostile.py"
    history_database = user_ipython / "profile_default" / "history.sqlite"
    python_startup = home / "python-startup.py"
    profile_marker = tmp_path / "profile-marker"
    jupyter_marker = tmp_path / "jupyter-marker"
    python_startup_marker = tmp_path / "python-startup-marker"
    workspace.mkdir()
    profile_startup.parent.mkdir(parents=True)
    user_jupyter.mkdir(parents=True)
    profile_startup.write_text(f"from pathlib import Path\nPath({str(profile_marker)!r}).touch()\n", encoding="utf-8")
    (user_jupyter / "jupyter_config.py").write_text(f"from pathlib import Path\nPath({str(jupyter_marker)!r}).touch()\n", encoding="utf-8")
    python_startup.write_text(f"from pathlib import Path\nPath({str(python_startup_marker)!r}).touch()\n", encoding="utf-8")
    history_database.write_bytes(b"host history must remain untouched")
    original_history_stat = history_database.stat()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("IPYTHONDIR", str(user_ipython))
    monkeypatch.setenv("JUPYTER_CONFIG_DIR", str(user_jupyter))
    monkeypatch.setenv("PYTHONSTARTUP", str(python_startup))
    monkeypatch.setenv("LOOMMUX_TEST_PRESERVED", "preserved")
    session = IPythonSession()
    second_root: Path | None = None
    try:
        assert session.start_workspace(workspace, "launch_cwd")["ok"] is True
        first_kernel = session.kernel
        assert first_kernel is not None and first_kernel.launch is not None
        first_launch = first_kernel.launch
        _assert_private_root(first_launch.runtime_root, workspace, home)
        _assert_kernel_policy(session, first_launch)
        _assert_hostile_user_state_is_untouched(profile_marker, jupyter_marker, python_startup_marker, history_database, original_history_stat.st_mtime_ns)

        reset = session.restart()

        assert reset["status"] == "restarted"
        assert not first_launch.runtime_root.exists()
        second_kernel = session.kernel
        assert second_kernel is not None and second_kernel.launch is not None
        second_launch = second_kernel.launch
        second_root = second_launch.runtime_root
        assert second_root != first_launch.runtime_root
        _assert_private_root(second_root, workspace, home)
        _assert_kernel_policy(session, second_launch)
        _assert_hostile_user_state_is_untouched(profile_marker, jupyter_marker, python_startup_marker, history_database, original_history_stat.st_mtime_ns)
        assert session.read_output(1, "stdout")["text"]
        assert session.run_cell("'after reset'")["execution"] == 3
    finally:
        session.close()
    assert second_root is not None
    assert not second_root.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object containment is Windows-specific")
def test_reset_kills_windows_kernel_descendants(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = IPythonSession()
    assert session.start_workspace(workspace, "launch_cwd")["ok"] is True
    try:
        running = session.run_cell(
            "# loommux: --wait 0.1\n"
            "import subprocess\n"
            "import sys\n"
            "import time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            "print(child.pid, flush=True)\n"
            "time.sleep(30)"
        )
        deadline = time.monotonic() + 3
        child_pid: int | None = None
        while time.monotonic() < deadline:
            text = str(session.read_output(running["execution"], "stdout")["text"])
            if text.strip().isdigit():
                child_pid = int(text.strip())
                break
            time.sleep(0.05)
        assert child_pid is not None

        assert session.restart()["status"] == "restarted"
        assert _wait_for_windows_process_exit(child_pid)
    finally:
        session.close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups are Unix-specific")
def test_reset_kills_unix_kernel_descendants(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = IPythonSession()
    assert session.start_workspace(workspace, "launch_cwd")["ok"] is True
    try:
        running = session.run_cell(
            "# loommux: --wait 0.1\n"
            "import subprocess\n"
            "import sys\n"
            "import time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            "print(child.pid, flush=True)\n"
            "time.sleep(30)"
        )
        child_pid = _wait_for_child_pid(session, running["execution"])
        assert child_pid is not None

        assert session.restart()["status"] == "restarted"
        assert _wait_for_posix_process_exit(child_pid)
    finally:
        session.close()


def test_kernel_exit_finishes_the_running_execution(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = IPythonSession()
    assert session.start_workspace(workspace, "launch_cwd")["ok"] is True
    old_kernel = session.kernel
    assert old_kernel is not None and old_kernel.launch is not None
    old_runtime_root = old_kernel.launch.runtime_root
    try:
        running = session.run_cell(
            "# loommux: --wait 0.1\n"
            "import os\n"
            "print('before-exit', flush=True)\n"
            "os._exit(17)"
        )
        deadline = time.monotonic() + 3
        status = session.execution_status(running["execution"])
        while status["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.05)
            status = session.execution_status(running["execution"])

        assert status["status"] == "killed"
        assert status["error"]["ename"] == "KernelProcessExited"
        assert session.status()["kernel_started"] is False
        assert session.kernel is old_kernel
        assert session.restart()["status"] == "restarted"
        assert not old_runtime_root.exists()
    finally:
        session.close()


def test_kernel_liveness_polls_the_process_without_blocking_an_event_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = KernelRuntime(workspace, Path(sys.executable).absolute())
    runtime.start(10)
    manager = runtime.manager
    assert manager is not None
    process = manager.provisioner.process

    def blocking_is_alive() -> bool:
        raise AssertionError("KernelManager.is_alive waits on a helper thread from inside an event loop")

    async def observe() -> bool:
        return runtime.is_alive()

    try:
        with monkeypatch.context() as patched:
            patched.setattr(manager, "is_alive", blocking_is_alive)
            assert asyncio.run(observe()) is True
            process.kill()
            process.wait(timeout=5)
            assert asyncio.run(observe()) is False
            assert runtime.returncode is not None
    finally:
        runtime.shutdown()


def test_kernel_lifecycles_close_the_event_loops_their_blocking_calls_create(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def open_loops() -> int:
        return sum(isinstance(item, asyncio.AbstractEventLoop) and not item.is_closed() for item in gc.get_objects())

    async def lifecycle() -> None:
        # The server drives sessions from worker threads the same way.
        session = IPythonSession()
        assert (await asyncio.to_thread(session.start_workspace, workspace, "launch_cwd"))["ok"] is True
        await asyncio.to_thread(session.run_cell, "1 + 1")
        await asyncio.to_thread(session.restart)
        await asyncio.to_thread(session.close)

    gc.collect()
    before = open_loops()
    # A full collection would free the loops anyway; an idle server never runs one.
    gc.disable()
    try:
        for _ in range(2):
            asyncio.run(lifecycle())
        after = open_loops()
    finally:
        gc.enable()

    assert after == before


@pytest.mark.skipif(sys.platform == "win32", reason="ZMQ IPC transport is POSIX-only")
def test_posix_kernels_use_private_ipc_sockets(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = IPythonSession()
    assert session.start_workspace(workspace, "launch_cwd")["ok"] is True
    kernel = session.kernel
    assert kernel is not None and kernel.launch is not None
    runtime_root = kernel.launch.runtime_root
    try:
        connection = json.loads(kernel.launch.connection_file.read_text())
        assert connection["transport"] == "ipc"
        assert Path(connection["ip"]).parent == runtime_root
        assert all(Path(f"{connection['ip']}-{connection[name]}").exists() for name in ("shell_port", "iopub_port", "stdin_port", "control_port", "hb_port"))
        assert session.run_cell("6 * 7")["result_text"] == "42"
    finally:
        session.close()
    assert not runtime_root.exists()


def _assert_kernel_policy(session: IPythonSession, launch: KernelLaunch) -> None:
    response = session.run_cell(
        "import os\n"
        "from IPython import get_ipython\n"
        "shell = get_ipython()\n"
        "print(os.environ['IPYTHONDIR'])\n"
        "print(os.environ['JUPYTER_CONFIG_DIR'])\n"
        "print(shell.history_manager.enabled)\n"
        "print(shell.cache_size)\n"
        "print(shell.colors)\n"
        "print({name: os.environ.get(name) for name in ('FORCE_COLOR', 'CLICOLOR', 'CLICOLOR_FORCE')})\n"
        "print(os.environ['LOOMMUX_TEST_PRESERVED'])\n"
        "print(shell.transform_cell('%%body\\n    >>> add(1, 2)\\n    ... 3\\n'))\n"
        "print('_loommux_it2' in shell.user_ns, '_loommux_tm' in shell.user_ns)"
    )

    assert response["status"] == "completed"
    lines = response["stdout"].splitlines()
    assert lines[:4] == [str(launch.ipython_dir), str(launch.jupyter_config_dir), "False", "0"]
    # IPython 8 reports the same no-colour policy as ``NoColor`` while newer
    # versions preserve the kernel trait spelling ``nocolor``.
    assert lines[4].lower() == "nocolor"
    assert lines[5] == "{'FORCE_COLOR': None, 'CLICOLOR': None, 'CLICOLOR_FORCE': None}"
    assert lines[6] == "preserved"
    # Doctest prompts in a cell body reach the magic unchanged.
    assert "    >>> add(1, 2)\\n    ... 3\\n" in lines[7]
    assert lines[-1] == "False False"


def _assert_hostile_user_state_is_untouched(profile_marker: Path, jupyter_marker: Path, python_startup_marker: Path, history_database: Path, original_mtime_ns: int) -> None:
    assert not profile_marker.exists()
    assert not jupyter_marker.exists()
    assert not python_startup_marker.exists()
    assert history_database.read_bytes() == b"host history must remain untouched"
    assert history_database.stat().st_mtime_ns == original_mtime_ns


def _assert_private_root(runtime_root: Path, workspace: Path, home: Path) -> None:
    assert runtime_root.is_dir()
    assert not _is_within(runtime_root, workspace)
    assert not _is_within(runtime_root, home)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _wait_for_windows_process_exit(pid: int) -> bool:
    import pywintypes
    import win32api
    import win32con

    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            process = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        except pywintypes.error:
            return True
        process.Close()
        time.sleep(0.05)
    return False


def _wait_for_child_pid(
    session: IPythonSession,
    execution: int,
) -> int | None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        text = str(session.read_output(execution, "stdout")["text"])
        if text.strip().isdigit():
            return int(text.strip())
        time.sleep(0.05)
    return None


def _wait_for_posix_process_exit(pid: int) -> bool:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        proc_stat = Path(f"/proc/{pid}/stat")
        if not proc_stat.exists():
            return True
        try:
            state = proc_stat.read_text().split()[2]
        except (FileNotFoundError, IndexError):
            return True
        if state == "Z":
            return True
        time.sleep(0.05)
    return False
