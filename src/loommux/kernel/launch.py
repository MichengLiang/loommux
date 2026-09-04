"""Build the private process inputs for one coding-agent kernel session."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

_REMOVED_ENVIRONMENT_VARIABLES = ("IPYTHONDIR", "JUPYTER_CONFIG_DIR", "CLICOLOR", "CLICOLOR_FORCE", "FORCE_COLOR")
_CONTROLLED_ENVIRONMENT = {
    "NO_COLOR": "1",
    "PY_COLORS": "0",
}
# ZMQInteractiveShell.init_environment enables child-process colours after
# process launch, so enforce the server policy again after shell initialization.
_IPYTHON_COLOR_CLEANUP = (
    "import os; "
    "os.environ.pop('CLICOLOR', None); "
    "os.environ.pop('CLICOLOR_FORCE', None); "
    "os.environ.pop('FORCE_COLOR', None)"
)


@dataclass(frozen=True)
class KernelLaunch:
    """The command, environment, and private files for one kernel process."""

    command: tuple[str, ...]
    environment: dict[str, str]
    runtime_root: Path
    ipython_dir: Path
    jupyter_config_dir: Path
    connection_file: Path

    @classmethod
    def create(cls, python_path: Path, workspace: Path) -> KernelLaunch:
        """Build inputs for one process without reusing user or workspace state."""
        runtime_root = _create_runtime_root(workspace)
        ipython_dir = runtime_root / "ipython"
        jupyter_config_dir = runtime_root / "jupyter"
        ipython_dir.mkdir()
        jupyter_config_dir.mkdir()
        connection_file = runtime_root / "kernel.json"
        environment = dict(os.environ)
        for name in _REMOVED_ENVIRONMENT_VARIABLES:
            environment.pop(name, None)
        environment.update(_CONTROLLED_ENVIRONMENT)
        environment["IPYTHONDIR"] = str(ipython_dir)
        environment["JUPYTER_CONFIG_DIR"] = str(jupyter_config_dir)
        command = (
            str(python_path),
            "-m",
            "ipykernel_launcher",
            "-f",
            str(connection_file),
            f"--ipython-dir={ipython_dir}",
            "--InteractiveShell.colors=nocolor",
            "--InteractiveShell.cache_size=0",
            "--HistoryManager.enabled=False",
            "--InteractiveShellApp.exec_PYTHONSTARTUP=False",
            f"--InteractiveShellApp.exec_lines={_IPYTHON_COLOR_CLEANUP}",
        )
        return cls(command, environment, runtime_root, ipython_dir, jupyter_config_dir, connection_file)


def _create_runtime_root(workspace: Path) -> Path:
    runtime_parent = Path(tempfile.gettempdir()).resolve()
    workspace = workspace.resolve()
    if _is_within(runtime_parent, workspace):
        raise RuntimeError("system temporary directory must be outside the workspace")
    runtime_root = Path(tempfile.mkdtemp(prefix="loommux-kernel-", dir=runtime_parent))
    # Windows places the standard per-user temporary directory under home.
    # The randomized private child, not the parent location, isolates Jupyter state.
    try:
        runtime_root.chmod(0o700)
    except OSError:
        pass
    return runtime_root


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
