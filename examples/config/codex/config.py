"""A loommux configuration directory for Codex: ``loommux --config examples/config/codex``.

Codex starts its MCP servers from wherever the agent is working, often a
subdirectory of the project. The workspace this resolver returns is the
project: the parent of the nearest ``.codex`` directory, or the launch
directory when there is none. loommux itself never looks for ``.codex``; this
directory has effect only when ``--config`` names it.
"""

from pathlib import Path


def resolve_workspace(launch_cwd: Path) -> Path:
    for directory in (launch_cwd, *launch_cwd.parents):
        if (directory / ".codex").is_dir():
            return directory
    return launch_cwd
