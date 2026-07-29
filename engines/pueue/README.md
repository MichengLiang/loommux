# Loommux Pueue Engine

`loommux-pueue` is the phase-one MCP stdio server for independent shell tasks
managed by an external Pueue daemon. It requires the paired `pueue` and
`pueued` `4.0.4` binaries and uses `pueue-lib = 0.31.1` through the typed
protocol. The server does not own the daemon. Daemon creation, stop, reset, and
clean operations remain outside this engine.

## Install And Start

Start `pueued` under the Pueue profile that should receive tasks. The engine
uses Pueue's normal settings discovery and fails before MCP tool readiness when
settings, the shared secret, the connection, or the protocol handshake is not
usable.

```bash
cargo build --release --locked --manifest-path engines/pueue/Cargo.toml
cargo install --path engines/pueue --locked
loommux-pueue
```

The default result mode returns model-readable MCP content. Use
`loommux-pueue --result-mode structured` when the client also needs
`structuredContent`. Diagnostics and tracing use stderr; stdout is reserved for
MCP protocol frames.

## Workspace

Without configuration, the canonical launch directory is the task workspace.
Set `LOOMMUX_WORKSPACE_CONFIG` to an absolute version 1 TOML file to select the
launch directory or a nearest-ancestor marker rule. Examples are available in
`examples/workspace-resolvers/`. Invalid configuration or a non-directory final
workspace prevents startup.

## Tools

The server exposes exactly seven tools:

| Tool | Operation |
| --- | --- |
| `run_shell` | Submit validated shell source and observe for its bounded initial wait. |
| `status` | Refresh the daemon connection and summarize local executions. |
| `execution_status` | Refresh one required execution without returning transcript text. |
| `read_output` | Read repeatable inclusive line ranges from combined output. |
| `search_output` | Search combined output with literal, regex, or automatic interpretation. |
| `wait` | Observe until terminal state or a caller-owned deadline. |
| `cancel` | Remove a queued/stashed task or request kill for a running/paused task. |

The positive integer returned as `execution` is the only public execution
coordinate. It is local to one server process and is required by every
execution-specific tool. Backend task identifiers, shared secrets, task
environment, and other clients' tasks do not enter MCP results.

## Operational Boundary

Pueue may continue a task after the MCP client closes stdin or the server exits.
The engine closes only its connection and local records. Operators must avoid
external edit, in-place restart, remove, clean, or reset operations on tasks
mapped by a live engine. External pause, resume, stash, enqueue, and
task-specific start remain observable scheduling changes. A missing or modified
mapped task becomes a stable observation error rather than a guessed lifecycle
transition.

Logs are read from the local Pueue data directory. Remote/TLS log collection is
outside phase one. The full normative behavior is maintained in
`docs/pueue-engine/design-specification.adoc`.

## Verification

From `engines/pueue/`, run:

```bash
just verify
just concurrency-20
```

`just verify` runs rustfmt, Clippy, nextest, line/function/region coverage,
dependency policy, locked debug and release builds, and `cargo package`. LLVM's
one compiler-generated async state-machine record is the only function
coverage exclusions; every authored `fn` declaration must have a nonzero count.
