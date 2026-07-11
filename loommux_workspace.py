"""Use Codex's project marker to select the loommux kernel workspace."""

from pathlib import Path


def resolve_workspace(launch_cwd: Path) -> Path:
    """Return the nearest Codex workspace root, or keep the launch directory."""
    for directory in (launch_cwd, *launch_cwd.parents):
        if (directory / ".codex").is_dir():
            return directory
    # A stdio MCP host's cwd is still the only reliable project context when
    # the caller has not created Codex project metadata.
    return launch_cwd
