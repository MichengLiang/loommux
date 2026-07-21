from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import pytest

from loommux.adapter import IPythonMCPAdapter
from loommux.coding_agent_kernel import KernelLaunch
from loommux.kernel_runtime import KernelRuntime, _kernel_spec, _KernelContainment


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

    monkeypatch.setattr("loommux.kernel_runtime.KernelLaunch.create", record_launch)
    monkeypatch.setattr("loommux.kernel_runtime._LoommuxKernelManager.start_kernel", fail_start_kernel)
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

    monkeypatch.setattr("loommux.kernel_runtime._LoommuxKernelManager", FailingManager)
    monkeypatch.setattr("loommux.kernel_runtime._create_containment", _KernelContainment)
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


def test_kernel_runtime_uses_an_independent_process_group_on_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    received: dict[str, object] = {}

    class ReadyClient:
        def start_channels(self) -> None:
            pass

        def wait_for_ready(self, *, timeout: float) -> None:
            assert timeout == 10

    class ReadyManager:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self.provisioner = type("Provisioner", (), {"pid": 456})()

        def start_kernel(self, **kwargs: object) -> None:
            received.update(kwargs)

        def client(self) -> ReadyClient:
            return ReadyClient()

        def shutdown_kernel(self, *, now: bool) -> None:
            assert now is True

    monkeypatch.setattr("loommux.kernel_runtime._LoommuxKernelManager", ReadyManager)
    monkeypatch.setattr("loommux.kernel_runtime._create_containment", _KernelContainment)
    monkeypatch.setattr("loommux.kernel_runtime.sys.platform", "win32")
    runtime = KernelRuntime(workspace, Path(sys.executable).absolute())
    try:
        runtime.start(10)
        assert received["independent"] is True
    finally:
        runtime.shutdown()


def test_kernel_launch_allows_the_platform_temp_directory_inside_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    temp_directory = home / "Temp"
    workspace.mkdir()
    temp_directory.mkdir(parents=True)
    monkeypatch.setattr("loommux.coding_agent_kernel.tempfile.gettempdir", lambda: str(temp_directory))

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
    adapter = IPythonMCPAdapter()
    second_root: Path | None = None
    try:
        assert adapter.start_workspace(workspace, "launch_cwd")["ok"] is True
        first_kernel = adapter.kernel
        assert first_kernel is not None and first_kernel.launch is not None
        first_launch = first_kernel.launch
        _assert_private_root(first_launch.runtime_root, workspace, home)
        _assert_kernel_policy(adapter, first_launch)
        _assert_hostile_user_state_is_untouched(profile_marker, jupyter_marker, python_startup_marker, history_database, original_history_stat.st_mtime_ns)

        reset = adapter.reset_python()

        assert reset["status"] == "restarted"
        assert not first_launch.runtime_root.exists()
        second_kernel = adapter.kernel
        assert second_kernel is not None and second_kernel.launch is not None
        second_launch = second_kernel.launch
        second_root = second_launch.runtime_root
        assert second_root != first_launch.runtime_root
        _assert_private_root(second_root, workspace, home)
        _assert_kernel_policy(adapter, second_launch)
        _assert_hostile_user_state_is_untouched(profile_marker, jupyter_marker, python_startup_marker, history_database, original_history_stat.st_mtime_ns)
        assert adapter.read_python_output(1, "stdout")["text"]
        assert adapter.run_python("'after reset'")["execution"] == 3
    finally:
        adapter.close()
    assert second_root is not None
    assert not second_root.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object containment is Windows-specific")
def test_reset_kills_windows_kernel_descendants(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = IPythonMCPAdapter()
    assert adapter.start_workspace(workspace, "launch_cwd")["ok"] is True
    try:
        running = adapter.run_python(
            "# loommux: timeout_seconds=0.1\n"
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
            text = str(adapter.read_python_output(running["execution"], "stdout")["text"])
            if text.strip().isdigit():
                child_pid = int(text.strip())
                break
            time.sleep(0.05)
        assert child_pid is not None

        assert adapter.reset_python()["status"] == "restarted"
        assert _wait_for_windows_process_exit(child_pid)
    finally:
        adapter.close()


def _assert_kernel_policy(adapter: IPythonMCPAdapter, launch: KernelLaunch) -> None:
    response = adapter.run_python(
        "import os\n"
        "from IPython import get_ipython\n"
        "shell = get_ipython()\n"
        "print(os.environ['IPYTHONDIR'])\n"
        "print(os.environ['JUPYTER_CONFIG_DIR'])\n"
        "print(shell.history_manager.enabled)\n"
        "print(shell.cache_size)\n"
        "print(shell.colors)\n"
        "print(os.environ['LOOMMUX_TEST_PRESERVED'])"
    )

    assert response["status"] == "completed"
    lines = response["stdout"].splitlines()
    assert lines[:4] == [str(launch.ipython_dir), str(launch.jupyter_config_dir), "False", "0"]
    # IPython 8 reports the same no-colour policy as ``NoColor`` while newer
    # versions preserve the kernel trait spelling ``nocolor``.
    assert lines[4].lower() == "nocolor"
    assert lines[5] == "preserved"


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
