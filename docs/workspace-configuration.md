# Workspace configuration

The canonical contract is [Coding Agent Control Plane Design, Section 5](coding-agent-control-plane-design.md#5-host-workspace-resolver).
`loommux` does not expose runtime workspace switching. By default, the MCP
host's launch cwd is the kernel workspace.

To authorize a dynamic rule, start loommux with `--config DIR` and define
`resolve_workspace(launch_cwd: Path) -> Path | str` in `DIR/config.py` (the
configuration directory is described in the
[Kernel Environment Design](kernel-environment-design.md), section 4).
Relative returns are resolved from the launch cwd. Invalid configuration
prevents server startup before tools are exposed; it never falls back to the
launch cwd.

`loommux` never searches for or executes `loommux_workspace.py`, `.loommux`,
`.codex`, or any other workspace-tree marker. The bundled
[Codex configuration directory](../examples/config/codex/config.py) is an
example and has effect solely when `--config` names it. It returns the parent of
the nearest `.codex` directory, or the launch cwd when there is no marker.

The kernel always uses the Python interpreter that started `loommux`; workspace
configuration cannot select another interpreter.
