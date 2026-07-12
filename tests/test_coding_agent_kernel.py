from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from loommux.adapter import IPythonMCPAdapter
from loommux.coding_agent_kernel import KernelLaunch
from loommux.kernel_session import KernelSession


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
        assert {name: launch.environment[name] for name in ("NO_COLOR", "PY_COLORS", "PAGER", "GIT_PAGER", "SYSTEMD_PAGER")} == {
            "NO_COLOR": "1",
            "PY_COLORS": "0",
            "PAGER": "cat",
            "GIT_PAGER": "cat",
            "SYSTEMD_PAGER": "cat",
        }
        assert not _is_within(launch.runtime_root, workspace)
        assert not _is_within(launch.runtime_root, home)
    finally:
        shutil.rmtree(launch.runtime_root, ignore_errors=True)


def test_kernel_session_start_failure_cleans_its_private_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    launches: list[KernelLaunch] = []
    original_create = KernelLaunch.create

    def record_launch(python_path: Path, launch_workspace: Path) -> KernelLaunch:
        launch = original_create(python_path, launch_workspace)
        launches.append(launch)
        return launch

    def fail_popen(*_args: object, **_kwargs: object) -> object:
        raise OSError("kernel process did not start")

    monkeypatch.setattr("loommux.kernel_session.KernelLaunch.create", record_launch)
    monkeypatch.setattr("loommux.kernel_session.subprocess.Popen", fail_popen)
    session = KernelSession(workspace, Path(sys.executable).absolute(), lambda _execution: None)

    with pytest.raises(OSError, match="kernel process did not start"):
        session.start()

    assert len(launches) == 1
    assert not launches[0].runtime_root.exists()
    assert session.launch is None
    session.shutdown()


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
    assert response["stdout"].splitlines() == [str(launch.ipython_dir), str(launch.jupyter_config_dir), "False", "0", "nocolor", "preserved"]


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
