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
- Terminal-formatted IOPub text normalized into ordinary append-only text
  transcripts before it reaches public output.
- A non-blocking execution model: a tool-call timeout ends only that MCP call;
  it does not terminate the Python cell.
- Explicit interrupt and kernel-reset operations, with preserved historical
  execution records after a reset.
- A single MCP entrypoint with content-only defaults and an explicit structured
  result mode.

`loommux` is intentionally not a multi-user notebook service, a durable job
queue, or a sandbox. Kernel state and execution records are memory-only and
belong to the lifetime of the server process.

## Requirements And Installation

loommux requires Python 3.10 or newer. The installed package brings the
runtime dependencies needed to launch an IPython kernel.

```bash
python -m pip install loommux
```

### Windows

Native Windows support covers Windows 10 and Windows 11 with CPython 3.10 or
newer. Install into the interpreter that the MCP host will use:

```powershell
py -m pip install loommux
```

The installed command is `loommux.exe`. Point an MCP host directly at that
executable rather than through a shell wrapper, and use an absolute Windows
workspace path for `cwd`:

```json
{
  "mcpServers": {
    "loommux": {
      "command": "C:\\workspace\\.venv\\Scripts\\loommux.exe",
      "args": ["--result-mode", "structured"],
      "cwd": "C:\\workspace"
    }
  }
}
```

loommux launches the kernel with the same interpreter as `loommux.exe`, keeps
its IPython and Jupyter state in a private temporary directory, and uses a
Windows Job Object so `reset_python` and server shutdown also end child
processes launched by the kernel. A submitted cell remains arbitrary Python:
commands inside that cell must target the operating system on which the kernel
is running. WSL is a separate Linux deployment, not a substitute for native
Windows coverage. Because IPython kernels do not accept Ctrl+C through this
entry point on Windows, `interrupt_python` replaces the private kernel after
marking the active cell `interrupted`; later cells use the fresh kernel.

The package installs one console command:

```text
loommux                   content-only results over Studio stdio
loommux --server          content-only results over Streamable HTTP
```

`loommux` uses an MCP Studio or host's child-process stdio connection by
default. `--server` starts a Streamable HTTP service with configurable `--host`,
`--port`, and `--path`. Both forms return only `content` by default.
`--result-mode structured` is the explicit opt-in that additionally returns
`structuredContent`.

For development, use [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/MichengLiang/loommux.git
cd loommux
uv sync --group dev
```

## Default Studio Connection

`loommux` defaults to MCP stdio transport and returns model-oriented `content`
only. This is the Studio-compatible default and prevents a client from
preferring raw `structuredContent` over the presentation intended for the
model.

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

On startup, loommux resolves its workspace, builds a kernel launch from the
server interpreter, and starts the kernel before accepting MCP tools. Server
startup fails rather than exposing a partially configured execution service.

## HTTP Server And Structured Opt-In

`loommux --server` exposes the same tools, input schemas, execution behavior,
and model-readable text over Streamable HTTP. It remains content-only unless
`--result-mode structured` is explicitly supplied.

Start a loopback-only content-only HTTP service from the workspace you want the
kernel to use:

```bash
cd /absolute/path/to/your/workspace
loommux --server --host 127.0.0.1 --port 8801 --path /mcp
```

Its MCP endpoint is `http://127.0.0.1:8801/mcp`. `--result-mode structured` is
available only when a client genuinely needs the raw status object:

```bash
loommux --server --result-mode structured --host 127.0.0.1 --port 8801 --path /mcp
```

MCP Studio, Inspector, and other Streamable HTTP clients use the same endpoint
URL. There is no separate Studio protocol. The complete matrix, subprocess
configuration examples, and security guidance are in
[MCP Connection Guide](docs/mcp-connections.md).

HTTP is a deployment boundary, not a different execution model: the
`execution` sequence, tools, output streams, workspace resolution, and
presentation rules are the same as the stdio server. Binding it beyond the
local machine exposes arbitrary Python execution and requires network controls
and authentication outside loommux.

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

All tools below are exposed by the single loommux entrypoint. Calls that take an
optional `execution` share one selection rule: an explicitly supplied positive
integer selects that record; otherwise loommux selects the current running
record, then the most recently accepted record. With neither, the tool returns
`execution_not_found`.

| Tool | Purpose |
| --- | --- |
| `run_python(freeform)` | Submit one loommux Python cell to the persistent kernel and wait for its initial result. |
| `python_status()` | Inspect the workspace, its authored source category, interpreter, kernel PID, busy state, and current or recent execution. |
| `python_execution_status(execution=None)` | Inspect lifecycle and diagnostic metadata without returning the full output body. |
| `read_python_output(...)` | Read a selected execution stream, optionally by line range and with per-line clipping. |
| `search_python_output(...)` | Search a selected output stream using literal text or regular expressions. |
| `wait_python(execution=None, timeout_seconds=30)` | Wait for an execution without interrupting it. |
| `interrupt_python()` | Send an interrupt signal to the current running execution. |
| `reset_python()` | Restart the kernel while preserving execution records and the server-local sequence. |

### Submitting A Cell

`run_python` accepts one `freeform` loommux Python cell. Ordinary source and
the resulting Python values of validated Apply Patch literals are available to
later cells in the same persistent server process.

```python
import math
radius = 3
math.pi * radius**2
```

### Apply Patch Literals

Use an outer triple-double-quoted literal containing a valid Apply Patch
program to pass patch text through a Python cell. The exact `*** Begin Patch`
and `*** End Patch` markers, valid file-operation controls, and hunk lines are
validated before loommux converts the literal into an equivalent Python `str`.
The patch text remains part of the resulting value, including embedded triple
quotes, backslashes, and braces.

````python
patch = f"""
*** Begin Patch
*** Update File: example.py
@@
+message = r"""
+hello
+"""
*** End Patch
"""
````

`patch` contains the complete Apply Patch program. The outer `r` and `f`
prefixes do not apply raw-string or f-string interpretation to the converted
patch text. Marker-shaped text with invalid patch grammar is ordinary Python
source and is not converted. See [Apply Patch Literal Transform Design](docs/ipython-mcp-protected-multiline-string-design.md)
for the full contract and acceptance rules.

The default initial wait for one MCP call is 10 seconds. A cell can make its
complete submission policy explicit with a Loommux control directive:

```python
# loommux: --wait 120
build_report()
```

`--wait` only changes how long that `run_python` call waits. It does not limit
Python runtime, interrupt the cell when time expires, modify later calls, or
add a variable to the kernel. A malformed or duplicated option returns
`invalid_loommux_directive` before an execution is allocated or source is
submitted.

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

When the caller has determined that the selected stream must be consumed in
full, omit `line_range`. `read_python_output` returns all of its lines in one
response, so there is no need to divide the read into consecutive small ranges.

`max_chars` clips each returned line without changing stored text or line
coordinates. `search_python_output` supports `literal`, `regex`, and `auto`
matching. In `auto` mode, loommux treats the query as a regular expression
when it compiles and falls back to literal matching when it does not. Search
results preserve original line numbers, mark matching lines with `M`, and
mark selected context lines with `C`.

### Requesting Complete Output

When a cell's entire terminal combined output is the intended result, include
`--full-output` in a Loommux control directive:

```python
# loommux: --full-output
build_report()
```

The option applies only to that execution. Once the execution is terminal, it
bypasses the normal 300-line delivery threshold and makes `run_python` or a
later `wait_python` return the complete collected `combined` output. It does
not cause partial running output to be returned and does not alter the input
or behavior of `read_python_output` and `search_python_output`.

The full-output and wait options are independent and may appear in the same
directive:

```python
# loommux: --wait 120 --full-output
build_report()
```

They may also be split across two directives:

```python
# loommux: --wait 120
# loommux: --full-output
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

## Security

Arbitrary Python execution is loommux's central capability. Treat the MCP
client, its process account, installed packages, the selected workspace, and
any reachable network endpoint as parts of the same security boundary. Give
the server access only to files, environments, and network resources the MCP
client is authorized to use.

The stdio server is generally the least exposed deployment mode. The HTTP
server must never be placed directly on an untrusted network. For a
vulnerability in loommux itself, use the private reporting process in
[SECURITY.md](SECURITY.md), not a public issue.

## Architecture And Documentation

The runtime is deliberately divided into narrow responsibilities:

- `host_workspace_config.py` and `workspace_resolver.py` own the MCP host's
  explicit workspace authorization and its public source category.
- `coding_agent_kernel.py` builds `KernelLaunch`: the server-interpreter
  command, controlled child environment, and one private runtime root.
- `kernel_runtime.py` owns the private root and delegates platform-specific
  kernel lifecycle operations to Jupyter's `KernelManager`; Windows child
  process containment is isolated there.
- `kernel_session.py` owns IOPub collection and execution correlation without
  invoking operating-system process APIs.
- `terminal_text.py` normalizes terminal controls before public text is stored.
- `execution.py` and `output_log.py` own normalized execution records and the
  append-only stream projections, line ranges, clipping, and search behavior.
- `adapter.py` owns lifecycle, execution-number allocation, selection, and
  control operations.
- `presentation.py` projects public state into model-readable text.
- `mcp_server_factory.py` registers the shared tools; the command entrypoint
  selects the result mode and transport.

The current public contract is documented in [Coding Agent Control Plane
Design](docs/coding-agent-control-plane-design.md).
Focused references cover [freeform cell control](docs/ipython-mcp-freeform-run-python-design.md),
[complete-output control](docs/ipython-mcp-full-output-directive-design.md),
[workspace configuration](docs/workspace-configuration.md), and the
[changelog](CHANGELOG.md).

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
file, the runtime package, tests, and public documentation while excluding
workspace-only material.

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution expectations.

## License

Copyright 2026 MichengLiang.

loommux is licensed under the [Apache License, Version 2.0](LICENSE).
