"""Resolve the kernel workspace from the MCP server process environment.

The server process cwd is the only default source of project context.  A
nearby ``loommux_workspace.py`` is deliberately executable Python rather than
a data format so callers can express their own project-discovery rules.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

CONFIG_FILENAME = "loommux_workspace.py"


def resolve_workspace_launch() -> tuple[Path, Path]:
    """Return the workspace and interpreter for the current server process."""
    launch_cwd = Path.cwd().resolve()
    config_path = _find_config(launch_cwd)
    if config_path is None:
        return launch_cwd, Path(sys.executable).absolute()

    module = _load_config(config_path)
    workspace = _resolve_workspace(module, launch_cwd)
    return workspace, Path(sys.executable).absolute()


def _find_config(launch_cwd: Path) -> Path | None:
    for directory in (launch_cwd, *launch_cwd.parents):
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def _load_config(config_path: Path) -> ModuleType:
    if not config_path.is_file():
        raise RuntimeError(f"loommux workspace config does not exist: {config_path}")
    spec = importlib.util.spec_from_file_location("_loommux_workspace_config", config_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load loommux workspace config: {config_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolve_workspace(module: ModuleType, launch_cwd: Path) -> Path:
    resolver = getattr(module, "resolve_workspace", None)
    candidate = resolver(launch_cwd) if callable(resolver) else getattr(module, "WORKSPACE", launch_cwd)
    return _resolve_path(candidate, launch_cwd, "workspace")


def _resolve_path(value: object, base: Path, label: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise RuntimeError(f"loommux {label} configuration must return a str or pathlib.Path")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve(strict=False)
