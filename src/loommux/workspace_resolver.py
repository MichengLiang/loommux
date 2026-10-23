"""Resolve the server-owned kernel workspace before MCP tools are exposed."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

WorkspaceResolver = Callable[[Path], str | Path]


class WorkspaceConfigError(RuntimeError):
    """A workspace resolution failure with its public classification."""

    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class WorkspaceResolution:
    """The workspace and the public category that authored its selection."""

    workspace: Path
    workspace_resolution: str


def resolve_workspace_launch(resolver: WorkspaceResolver | None = None) -> WorkspaceResolution:
    """Resolve the launch cwd, or ask the resolver a ``--config`` directory's ``config.py`` defines."""
    launch_cwd = Path.cwd().resolve()
    if resolver is None:
        return WorkspaceResolution(workspace=_validate_workspace(launch_cwd), workspace_resolution="launch_cwd")

    try:
        value = resolver(launch_cwd)
    except Exception as exc:
        # The status is what reaches the MCP surface; the resolver's own detail
        # stays in the chained exception for whoever started the server.
        raise WorkspaceConfigError("workspace_config_load_failed", "workspace resolver failed while executing") from exc
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
