"""Codex-oriented workspace resolver for an explicitly configured MCP host."""

from __future__ import annotations

from pathlib import Path


def resolve_workspace(launch_cwd: Path) -> Path:
    """Return the parent of the nearest ``.codex`` directory, or launch cwd."""
    for directory in (launch_cwd, *launch_cwd.parents):
        if (directory / ".codex").is_dir():
            return directory
    return launch_cwd
