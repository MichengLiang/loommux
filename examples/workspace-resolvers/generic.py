"""Explicit loommux workspace resolver example.

Set LOOMMUX_WORKSPACE_CONFIG to this file's absolute path to use it. The
package never discovers or loads this example from a workspace tree.
"""

from pathlib import Path


def resolve_workspace(launch_cwd: Path) -> Path:
    """Keep the MCP host's launch directory as the kernel workspace."""
    return launch_cwd
