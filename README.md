# loommux

[![CI](https://github.com/MichengLiang/loommux/actions/workflows/ci.yml/badge.svg)](https://github.com/MichengLiang/loommux/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/loommux.svg)](https://pypi.org/project/loommux/)
[![Python](https://img.shields.io/pypi/pyversions/loommux.svg)](https://pypi.org/project/loommux/)
[![License](https://img.shields.io/pypi/l/loommux.svg)](LICENSE)

`loommux` is a [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
server for persistent, inspectable IPython work. One loommux server process
owns one IPython kernel. Python variables, imports, definitions, and other
kernel namespace state survive from one submitted cell to the next, while each
accepted cell receives a stable, server-local integer execution number.

The project is for MCP clients and agents that need more than a one-shot
subprocess. It makes a running cell observable without losing it: callers can
wait later, inspect its state, read a selected output stream by line range,
search retained output, interrupt the active cell, or restart the kernel.

## What It Provides

- One persistent IPython kernel per loommux server process.
- A strictly increasing positive integer `execution` coordinate for every
  accepted cell during that server process's lifetime.
- In-memory output retained separately as `combined`, `stdout`, `stderr`,
  `result`, and `traceback` streams.
- IOPub-order `combined` output, including IPython-style `Out[execution]:`
  labels for display results.
- A non-blocking execution model: a tool-call timeout ends only that MCP call;
  it does not terminate the Python cell.
- Explicit interrupt and kernel-reset operations, with preserved historical
  execution records after a reset.
- Two fixed MCP result-channel entrypoints for clients with different result
  handling requirements.
- An optional local browser monitor for observing tool calls and execution
  lifecycle events without granting browser-side Python control.

`loommux` is intentionally not a multi-user notebook service, a durable job
queue, or a sandbox. Kernel state and execution records are memory-only and
belong to the lifetime of the server process.

## Requirements And Installation

loommux requires Python 3.13 or newer. The installed package brings the
runtime dependencies needed to launch an IPython kernel.

```bash
python -m pip install loommux
```

The package installs two console commands:

```text
loommux          Standard MCP server over stdio
loommux-content  Content-only MCP server over streamable HTTP
```

For development, use [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/MichengLiang/loommux.git
cd loommux
uv sync --group dev
```

## Standard MCP Server

`loommux` is the standard entrypoint. It uses MCP stdio transport and returns
both model-oriented text content and a structured public status object. This
is the appropriate entrypoint for an MCP host that supports normal
`structuredContent` handling.

The server process's working directory is the default kernel workspace. A
generic MCP configuration therefore assigns the desired project directory as
the command's `cwd`:

```json
{
  "mcpServers": {
    "loommux": {
      "command": "loommux",
      "cwd": "/absolute/path/to/your/workspace"
    }
  }
}
```

The exact enclosing configuration shape depends on the MCP host. The material
facts are that the host starts `loommux`, the process runs in the intended
workspace, and the Python environment running `loommux` can import
`ipykernel`.

On startup, loommux resolves its workspace, verifies that its own Python
interpreter can import `ipykernel`, and starts the kernel before accepting MCP
tools. Server startup fails rather than exposing a partially configured
execution service when these conditions are not met.

## Content-Only HTTP Server

`loommux-content` exposes the same tools, input schemas, execution behavior,
and model-readable text as the standard server. Its results deliberately omit
`structuredContent`. Use it for a client that cannot consume structured MCP
results correctly or must only receive text content.

Start it from the workspace you want the kernel to use:

```bash
cd /absolute/path/to/your/workspace
loommux-content
```

It runs FastMCP's streamable-HTTP transport on port `8801` and binds to
`0.0.0.0`; clients normally connect to FastMCP's streamable MCP endpoint on
that service. This is a deployment boundary, not a different execution model:
the `execution` sequence, tools, output streams, and presentation rules are
the same as the stdio server.

Because this entrypoint executes arbitrary Python and listens on all
interfaces, do not expose it directly to untrusted networks. Put it behind
network controls and authentication appropriate to the environment, or prefer
the stdio server.

## Workspace And Interpreter

Workspace selection occurs when the server process starts. loommux does not
provide a runtime tool that changes the workspace or Python interpreter.

By default, the server's current working directory is the workspace and the
interpreter that launched `loommux` launches the kernel. This preserves the
same virtual environment that imported loommux and avoids an ambiguous second
Python-selection mechanism.

`LOOMMUX_WORKSPACE_CONFIG` is the only optional workspace configuration
entrance. Set it to the absolute path of a trusted Python resolver defining
`resolve_workspace(launch_cwd: Path) -> Path | str`. loommux never searches or
executes `loommux_workspace.py`, `.codex`, or any other workspace-tree file or
marker. Resolver failures prevent startup before tools are available.

The [generic](examples/workspace-resolvers/generic.py) and
[Codex](examples/workspace-resolvers/codex.py) resolver examples are inert
until explicitly selected through that environment variable. See [workspace
configuration](docs/workspace-configuration.md) and the canonical [Coding
Agent Control Plane Design](docs/coding-agent-control-plane-design.md#5-host-workspace-resolver)
for the complete contract.

## Execution Model

Each accepted `run_python` submission creates an execution record with one
public identity:

```text
execution: positive integer
```

The sequence begins at `1` for a new loommux server process and increases only
when a cell is accepted. loommux accepts one running cell at a time. A second
`run_python` call while the kernel is busy is rejected with `status="busy"`;
it is not queued.

An execution can be `running`, `completed`, `error`, `interrupted`, or
`killed`. Python errors are recorded execution states, not MCP transport
failures. The error summary identifies the exception while the collected
traceback remains available from the execution's `traceback` stream.

The integer is owned by loommux rather than copied from IPython's kernel-local
execution counter. It stays stable for the server process, including across
`reset_python`. When a cell yields a `text/plain` display result, loommux
authors the combined log with its own stable coordinate:

```text
Out[5]: 42
```

After a reset, the replacement IPython kernel may have restarted its internal
counter, but the next loommux execution number remains consecutive and prior
records remain readable.

## MCP Tools

All tools below are exposed by both server entrypoints. Calls that take an
optional `execution` share one selection rule: an explicitly supplied positive
integer selects that record; otherwise loommux selects the current running
record, then the most recently accepted record. With neither, the tool returns
`execution_not_found`.

| Tool | Purpose |
| --- | --- |
| `run_python(freeform)` | Submit one raw Python cell to the persistent kernel and wait for its initial result. |
| `python_status()` | Inspect the workspace, interpreter, kernel PID, busy state, and current or recent execution. |
| `python_execution_status(execution=None)` | Inspect lifecycle and diagnostic metadata without returning the full output body. |
| `read_python_output(...)` | Read a selected execution stream, optionally by line range and with per-line clipping. |
| `search_python_output(...)` | Search a selected output stream using literal text or regular expressions. |
| `wait_python(execution=None, timeout_seconds=30)` | Wait for an execution without interrupting it. |
| `interrupt_python()` | Send an interrupt signal to the current running execution. |
| `reset_python()` | Restart the kernel while preserving execution records and the server-local sequence. |

### Submitting A Cell

`run_python` accepts a raw `freeform` Python cell. The source is submitted
unchanged, so variables and imports are immediately available to later cells
in the same server process.

```python
import math
radius = 3
math.pi * radius**2
```

The default wait for this one MCP call is 10 seconds. A cell can request a
different positive wait time by containing exactly one complete directive
line:

```python
# loommux: timeout_seconds=120
build_report()
```

The directive only changes how long that `run_python` call waits. It does not
limit Python runtime, interrupt the cell when time expires, modify later
calls, or add a variable to the kernel. No valid directive, an invalid
directive, or multiple valid directives uses the 10-second default.

When the call returns while the cell is still running, use `wait_python`,
`python_execution_status`, `read_python_output`, `search_python_output`,
`interrupt_python`, or `reset_python` to continue observing or controlling the
same execution.

### Output Streams And Long Output

Each execution retains five append-only text projections:

| Stream | Contents |
| --- | --- |
| `combined` | stdout, stderr, display results, and tracebacks in IOPub arrival order. |
| `stdout` | Python stdout stream events. |
| `stderr` | Python stderr stream events. |
| `result` | `text/plain` from IPython execute-result and display-data events. |
| `traceback` | Traceback text from Python error events. |

Completed combined output of at most 300 lines is returned by `run_python` and
`wait_python` beneath an `In [execution]:` header. A display result then keeps
its IPython-style `Out[execution]:` line; a silent cell returns only the input
header, and stdout or traceback remains in its original combined order. For an
execution that is still running, or for an unmarked terminal execution whose
combined output exceeds 300 lines, the response retains the record but omits
the full body. The output is not discarded; read or search it through the
output tools.

`read_python_output` uses `start:stop` inclusive line coordinates. Positive
endpoints are 1-indexed, endpoints may be omitted, and negative endpoints
count from the end of the selected stream:

```text
:10    first 10 lines
-10:   final 10 lines
20:40  lines 20 through 40
3:3    only line 3
```

`max_chars` clips each returned line without changing stored text or line
coordinates. `search_python_output` supports `literal`, `regex`, and `auto`
matching. In `auto` mode, loommux treats the query as a regular expression
when it compiles and falls back to literal matching when it does not. Search
results preserve original line numbers, mark matching lines with `M`, and
mark selected context lines with `C`.

### Requesting Complete Output

When a cell's entire terminal combined output is the intended result, include
this exact no-value Python comment in that cell:

```python
# loommux: full_output
build_report()
```

The marker applies only to that execution. Once the execution is terminal, it
bypasses the normal 300-line delivery threshold and makes `run_python` or a
later `wait_python` return the complete collected `combined` output. It does
not cause partial running output to be returned and does not alter the input
or behavior of `read_python_output` and `search_python_output`.

The full-output and timeout directives are independent and may appear in the
same cell:

```python
# loommux: timeout_seconds=120
# loommux: full_output
build_report()
```

## Interrupting And Resetting

`interrupt_python` requests an interrupt for the current running cell. An
`interrupt_sent` response only confirms signal delivery; the execution reaches
its final state after the kernel reports IOPub `idle`.

`reset_python` is stronger: it stops the existing kernel and starts a
replacement in the same workspace with the same interpreter. A running record
is marked `killed`. Reset does not erase stored executions, their output, or
the sequence counter, so historical records can still be read by their
integer `execution` value and the next accepted cell receives the next number.

Stopping the loommux server ends the session. The kernel, namespace,
execution-record table, output streams, and sequence are not persisted to
disk; a new server process begins a fresh sequence at `1`.

## Optional Local Monitor

The repository contains an optional [`monitor/`](monitor/) application. It is
not part of the PyPI package distribution. It receives a bounded stream of
observation events, retains them only in memory, and renders recent Python
code, output, status, and tool activity in a browser. It does not execute
Python, interrupt executions, reset kernels, or change workspaces.

From a source checkout:

```bash
cd monitor
pnpm install
pnpm dev
```

The monitor service defaults to `http://127.0.0.1:9765`. loommux publishes to
`http://127.0.0.1:9765/api/events` by default. Publishing runs in a bounded
background path: an unavailable monitor, delivery failure, or queue overflow
does not change MCP tool results or kernel behavior.

Configuration:

| Variable | Meaning |
| --- | --- |
| `LOOMMUX_MONITOR_URL` | Override the monitor event-ingest URL. |
| `LOOMMUX_MONITOR_DISABLED=1` | Disable monitor publishing. |

Monitor events can contain submitted code, stdout, stderr, display results,
tracebacks, tool arguments, and result summaries. Treat them as sensitive
execution telemetry. The monitor is localhost-only by default; do not expose
it publicly without applying appropriate security controls. More operational
details are in [monitor/README.md](monitor/README.md).

## Security

Arbitrary Python execution is loommux's central capability. Treat the MCP
client, its process account, installed packages, the selected workspace, and
any reachable network endpoint as parts of the same security boundary. Give
the server access only to files, environments, and network resources the MCP
client is authorized to use.

The stdio server is generally the least exposed deployment mode. The HTTP
entrypoint must never be placed directly on an untrusted network. For a
vulnerability in loommux itself, use the private reporting process in
[SECURITY.md](SECURITY.md), not a public issue.

## Architecture And Documentation

The runtime is deliberately divided into narrow responsibilities:

- `execution.py` owns an execution record and its terminal state.
- `output_log.py` owns the append-only stream logs, line ranges, clipping, and
  search behavior.
- `kernel_session.py` starts the IPython subprocess and collects IOPub events.
- `adapter.py` owns kernel lifecycle, execution-number allocation, selection,
  and control operations.
- `presentation.py` turns public status into the model-readable text surface.
- `mcp_server_factory.py` registers the shared tools; the two entry modules
  select result-channel policy and transport.
- `monitoring.py` publishes observation events without participating in
  execution authority.

The current public contract is documented in [Coding Agent Control Plane
Design](docs/coding-agent-control-plane-design.md).
Focused references cover the [freeform timeout directive](docs/ipython-mcp-freeform-run-python-design.md),
the [complete-output directive](docs/ipython-mcp-full-output-directive-design.md),
and [workspace configuration](docs/workspace-configuration.md). Some files in
`docs/` are explicitly retained as historical design material; they are not
current API specifications.

## Development And Release Checks

Run the Python checks from the repository root:

```bash
uv run pytest
uv run ruff check src tests
uv run basedpyright src
uv build --out-dir dist
uv run twine check dist/*
```

The project metadata declares this README as the package readme:

```toml
[project]
readme = "README.md"
```

Consequently, the same document is rendered on PyPI when a new release is
built and uploaded. The explicit source-distribution allowlist includes this
file, the runtime package, tests, and public documentation while excluding the
local monitor's Node dependencies and workspace-only material.

For the optional monitor, run its checks from `monitor/`:

```bash
pnpm typecheck
pnpm test
pnpm build
pnpm e2e
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution expectations.

## License

Copyright 2026 MichengLiang.

loommux is licensed under the [Apache License, Version 2.0](LICENSE).
