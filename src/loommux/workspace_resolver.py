"""Resolve the server-owned kernel workspace before MCP tools are exposed."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from loommux.host_workspace_config import WorkspaceConfigError, execute_workspace_resolver, load_workspace_resolver


@dataclass(frozen=True)
class WorkspaceResolution:
    """The workspace and the public category that authored its selection."""

    workspace: Path
    workspace_resolution: str


def resolve_workspace_launch() -> WorkspaceResolution:
    """Resolve the launch cwd or the one resolver explicitly authorized by env."""
    launch_cwd = Path.cwd().resolve()
    resolver = load_workspace_resolver(os.environ)
    if resolver is None:
        return WorkspaceResolution(workspace=_validate_workspace(launch_cwd), workspace_resolution="launch_cwd")

    value = execute_workspace_resolver(resolver, launch_cwd)
    if not isinstance(value, str | Path):
        raise WorkspaceConfigError("workspace_config_invalid_return", "workspace resolver must return str or pathlib.Path")
    return WorkspaceResolution(workspace=_validate_workspace(_resolve_path(value, launch_cwd)), workspace_resolution="explicit_config")


def _resolve_path(value: str | Path, launch_cwd: Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = launch_cwd / candidate
    return candidate.resolve(strict=False)


def _validate_workspace(workspace: Path) -> Path:
    if not workspace.exists():
        raise WorkspaceConfigError("workspace_not_found", "resolved workspace does not exist")
    if not workspace.is_dir():
        raise WorkspaceConfigError("workspace_not_directory", "resolved workspace is not a directory")
    return workspace
