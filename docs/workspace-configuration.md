# Workspace configuration

`loommux` does not expose a runtime workspace-switching tool. The MCP client
launches the server process, and that process cwd is the default workspace.
The persistent IPython kernel starts before MCP tools are accepted and runs in
that directory. Its default Python interpreter is the one that launched the
server.

This keeps workspace selection at the process boundary where the MCP client
already controls it. For stdio clients, set the server command's `cwd` to the
directory that should be used as the workspace.

## Optional Python configuration

This project ships [loommux_workspace.py](../loommux_workspace.py). When the
server starts from this project or one of its descendants, it walks upward from
the server cwd and uses the nearest directory containing `.codex` as the
workspace root. This is the normal configuration for a Codex-launched MCP
server, because Codex creates that hidden directory at its workspace root.

For a different project-discovery rule, copy
[`loommux_workspace.py.example`](../loommux_workspace.py.example) to
`loommux_workspace.py` in the server cwd or any parent directory and replace
the project file's rule. The first such file found while walking upward is
loaded as Python. It may define:

```python
from pathlib import Path


def resolve_workspace(launch_cwd: Path) -> Path:
    # Any Python discovery rule is valid: parent, child, marker search, etc.
    return launch_cwd.parents[1] / "notebooks"
```

`WORKSPACE = "..."` can replace `resolve_workspace`. The Python interpreter is
not configurable here: loommux always starts the kernel with the interpreter
that launched the MCP server. This preserves the environment that successfully
imported loommux and avoids an additional, ambiguous Python-path contract.
