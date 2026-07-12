# Workspace configuration

The canonical contract is [Coding Agent Control Plane Design, Section 5](coding-agent-control-plane-design.md#5-host-workspace-resolver).
`loommux` does not expose runtime workspace switching. By default, the MCP
host's launch cwd is the kernel workspace.

To authorize a dynamic rule, set `LOOMMUX_WORKSPACE_CONFIG` to the absolute
path of a trusted Python file defining `resolve_workspace(launch_cwd: Path) ->
Path | str`. Relative returns are resolved from the launch cwd. Invalid
configuration prevents server startup before tools are exposed; it never falls
back to the launch cwd.

`loommux` never searches for or executes `loommux_workspace.py`, `.codex`, or
any other workspace-tree marker. The bundled [generic](../examples/workspace-resolvers/generic.py)
and [Codex](../examples/workspace-resolvers/codex.py) resolvers are examples
only and have effect solely when their absolute path is explicitly configured.
The Codex example returns the parent of the nearest `.codex` directory, or the
launch cwd when there is no marker.

The manually started `loommux-content` entrypoint is an explicit host
composition: when this environment variable is unset, its `main()` selects the
bundled Codex resolver before the MCP server starts. A host-provided value still
has priority. This does not change the default for `create_mcp()` or the
standard `loommux` entrypoint.

The kernel always uses the Python interpreter that started `loommux`; workspace
configuration cannot select another interpreter.
