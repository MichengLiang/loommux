# Workspace configuration

The canonical contract is [Coding Agent Control Plane Design, Section 5](coding-agent-control-plane-design.md#5-host-workspace-resolver).
`loommux` does not expose runtime workspace switching. By default, the MCP
host's launch cwd is the kernel workspace.

A dynamic rule is `resolve_workspace(launch_cwd: Path) -> Path | str` in the
`config.py` of the configuration directory: the nearest `.loommux` at or above
the launch cwd, or the one `--config DIR` names (described in the
[Kernel Environment Design](kernel-environment-design.md), section 4).
Relative returns are resolved from the launch cwd. Invalid configuration
prevents server startup before tools are exposed; it never falls back to the
launch cwd.

`loommux` does not treat `.git`, `.codex` or any other tool's marker as its
own. The bundled
[Codex configuration directory](../examples/config/codex/config.py) is an
example, used with `--config examples/config/codex` or copied into a project's
`.loommux/`. It returns the parent of the nearest `.codex` directory, or the
launch cwd when there is no marker.

The kernel always uses the Python interpreter that started `loommux`; workspace
configuration cannot select another interpreter.
