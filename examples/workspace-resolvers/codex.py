"""Explicit Codex-oriented loommux workspace resolver example.

Set LOOMMUX_WORKSPACE_CONFIG to this file's absolute path to opt in. The
package itself does not search for .codex directories.
"""

from pathlib import Path


def resolve_workspace(launch_cwd: Path) -> Path:
    """Return the parent of the nearest .codex directory, or the launch cwd."""
    for directory in (launch_cwd, *launch_cwd.parents):
        if (directory / ".codex").is_dir():
            return directory
    return launch_cwd
