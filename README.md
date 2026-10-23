# loommux

[![CI](https://github.com/MichengLiang/loommux/actions/workflows/ci.yml/badge.svg)](https://github.com/MichengLiang/loommux/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/loommux.svg)](https://pypi.org/project/loommux/)
[![Python >=3.13](https://img.shields.io/badge/python-%3E%3D3.13-3776AB.svg?logo=python&logoColor=white)](https://pypi.org/project/loommux/)
[![License](https://img.shields.io/pypi/l/loommux.svg)](LICENSE)

`loommux` is a [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
server for persistent, inspectable IPython work. A loommux server owns logical
kernel resources on behalf of MCP sessions. Each resource has an independent
namespace, execution history, client lease set, and replaceable kernel process.
Python variables, imports, and definitions survive from one submitted cell to
the next inside that selected resource.

The project is for MCP clients and agents that need more than a one-shot
subprocess. It makes a running cell observable without losing it: callers can
wait later, inspect its state, read a selected output stream by line range,
search retained output, interrupt the active cell, or restart the kernel.

## What It Provides

- One main kernel per MCP session, private by default, shared by name when
  clients need one collaborative namespace, and child kernels that belong to
  it alone.
- One persistent IPython session, execution sequence, and retained history per
  logical resource.
- Activity, standard MCP-ping, or connection-bound leases with automatic
  orphan reclamation; a connection lease follows the client's open stream
  and alerts when the client stops behaving that way.
- A strictly increasing positive integer `execution` coordinate for every
  accepted cell during its logical resource's lifetime.
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
belong to their logical resource; server shutdown retires every resource.

## Requirements And Installation

loommux requires Python 3.13 or newer. The installed package brings the
runtime dependencies needed to launch an IPython kernel.

```bash
python -m pip install loommux
```

### Windows

Native Windows support covers Windows 10 and Windows 11 with CPython 3.13 or
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
Windows Job Object so `restart` and server shutdown also end child
processes launched by the kernel. A submitted cell remains arbitrary Python:
commands inside that cell must target the operating system on which the kernel
is running. WSL is a separate Linux deployment, not a substitute for native
Windows coverage. Because IPython kernels do not accept Ctrl+C through this
entry point on Windows, `interrupt` replaces the private kernel after
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
uv sync --locked --group dev
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
tools, resource-local execution sequences, output streams, workspace
resolution, and presentation rules are the same as the stdio server. The HTTP
application also serves a resource console at `/` and operational JSON APIs
under `/api`. Binding it beyond the local machine exposes arbitrary Python
execution and requires network controls and authentication outside loommux.

If a server stops answering (the console, new connections, and Ctrl+C all go
quiet), run `kill -USR1 <loommux pid>` on Linux or macOS. Every thread's Python
stack is printed to the server's stderr and the server keeps running. The
handler runs below the event loop, so it works while the loop is frozen;
include its output when reporting the hang.

On Linux with glibc, a long-running server with many concurrent clients holds
memory in per-thread malloc arenas that it has already freed. Starting it with
`MALLOC_ARENA_MAX=2` keeps that bounded: under the same two-minute load the
server's resident memory levelled off at 277 MB instead of growing past 600 MB.

## Kernel Resources And Client Leases

An ordinary MCP connection selects a kernel without changing the eight-tool
surface. Each MCP session works in one main kernel: without an additional
header, a private one named after its MCP Session ID; with
`X-Loommux-Resource`, a named main kernel shared by every session configured
with that name, where each participating session still holds an independent
lease. Kernels that a kernel opens by name are child kernels of that home (see
[Main Kernel Binding Design](docs/kernel-binding-design.md)).

The server chooses how a lease proves that its client is still there with the
`lease_mode` setting of its [configuration directory](#configuration-directory):

| Mode | The lease holds while | Fits |
| --- | --- | --- |
| `activity` (default) | tool calls keep arriving within 80 minutes (24 hours for named main kernels) | any client |
| `heartbeat` | the client pings within 60 seconds | clients that ping on their own, such as `LeaseAwareClient` |
| `connection` | the session keeps its standalone Streamable HTTP `GET` stream open; stdio sessions for the life of the process | clients that hold that stream while they run, such as Claude Code |

A connection lease survives any amount of idleness. About a minute after the
client exits (`connection_grace_seconds`, default 60), the session
counts as gone and its lease is released. A named main kernel then keeps
itself for the named window (`named_activity_timeout_seconds`, 24 hours) after the
last call, so a new session can attach to it by name; the console shows how
long it has left and can retire it at any time. An unnamed `private-<session>`
kernel is retired with the lease, since nobody comes back to a generated name.
If a session
calls tools without that stream, or keeps calling after it
closed, the lease falls back to the activity window and raises an alert:
every tool result of that session ends with a `Lease alert` block, and the
console at `/` turns its lease-signal row red. For Claude Code over HTTP:

```bash
loommux --server --host 127.0.0.1 --port 8801 --path /mcp
```

started in a directory whose `.loommux/config.py` holds:

```python
lease_mode = "connection"
```

The server publishes the current lease policy at `/api/lease-policy`. The
included `loommux.client.LeaseAwareClient` discovers and pins that policy
generation before initialization and sends standard MCP `ping` while a
heartbeat lease is active; under a connection policy its transport's own
standalone stream holds the lease:

```python
from loommux.client import LeaseAwareClient


async with LeaseAwareClient(
    "http://127.0.0.1:8801/mcp",
    "analysis-agent",
    resource_name="shared-analysis",
) as client:
    result = await client.call_tool(
        "run_cell",
        {"freeform": "value = 1\nprint(value)"},
    )
```

The operator label is optional. Omitting it or passing `None` leaves the
`X-Loommux-Operator` header absent, so the server uses its session-derived
fallback display name.

The complete runnable client-cooperation example is in
[examples/lease-aware-client](examples/lease-aware-client/README.md).

The complete identity, lifecycle, policy, orphan-execution, control API, and
source-ownership contract is documented in
[Kernel Resource Daemon Design](docs/kernel-resource-daemon-design.md).

### Controlling Kernels From A Kernel

Every kernel knows which resource it is and where its daemon listens
(`LOOMMUX_KERNEL_ID`, `LOOMMUX_KERNEL_NAME`, `LOOMMUX_CONTROL_URL`), and holds
a client of that daemon as `loommux`:

```python
loommux.kernels()             # this home: name, id, state, busy, idle, pid, executions, leases, kept, scope, home
loommux.kernels(timeout=2)    # the same, giving up with DaemonError after 2 seconds
loommux.self                  # this kernel
lab = loommux["lab"]          # this home's child kernel, by name or id; opened on first use
job = lab.run("train()", wait=5)
job.wait(600)                 # its state; job.output() reads its combined output
lab.restart()
loommux["lab"].recycle(force=True)
loommux.self.rename("report-cleanup")   # returns the name received; report-cleanup-2 if taken
loommux.notify("⚠ memory is low")      # to the agent, apart from this cell's output
```

A kernel opened this way is a child kernel of the asking kernel's home: the
main kernel an MCP session works in. It lives as long as that main kernel holds
a client lease and is released with it, and its name belongs to the home, so
another session's kernels neither list nor reach it. A kernel already running a
cell refuses `run()` with `DaemonError` whose `status` is `busy`; cells are
never queued.

`loommux.notify(text)` is how code in a kernel, an alarm in a `post_run_cell`
hook for instance, tells the agent something about this moment. A notice is
not output: no output stream holds it and the 5,000-token limit never omits it.
The next tool result ends with every notice no result has delivered, each
once, labeled `In [N] · ` when another cell spoke it and `Startup · ` when a
startup file did. A notice is cut at 500 characters and a cell keeps ten; a
cell forwarded with `--kernel build` speaks build's notices again as
`build · …`. The `idle` column, and `idle_since` in each snapshot, say how long
a kernel has run nothing.

The client speaks the HTTP control plane, so anything it does stays outside
the daemon process. Outside a kernel, `loommux.daemon.Daemon()` connects to
`LOOMMUX_CONTROL_URL` or `http://127.0.0.1:8801` and acts for the person
running loommux: it lists every kernel and resolves a main kernel by name or
any kernel by id, opening none, since nothing there can hold a new one. A
stdio server has no HTTP control plane; its kernels still know who they are,
and every request explains that no control plane exists.

## Workspace And Interpreter

Workspace selection occurs when the server process starts. loommux does not
provide a runtime tool that changes the workspace or Python interpreter.

By default, the server's current working directory is the workspace and the
interpreter that launched `loommux` launches the kernel. This preserves the
same virtual environment that imported loommux and avoids an ambiguous second
Python-selection mechanism.

A `config.py` in the [configuration directory](#configuration-directory) that
defines `resolve_workspace(launch_cwd: Path) -> Path | str` chooses another
workspace; it is the only way to. loommux does not treat `.git`, `.codex` or
any other tool's marker as its own. Resolver failures prevent startup before
tools are available.

The [Codex](examples/config/codex/config.py) configuration directory is an
example. See [workspace
configuration](docs/workspace-configuration.md) and the canonical [Coding
Agent Control Plane Design](docs/coding-agent-control-plane-design.md#5-host-workspace-resolver)
for the complete contract.

## Configuration Directory

loommux is configured in one directory, shaped like an IPython profile
directory: the nearest `.loommux` at or above the directory it starts in, as git
finds `.git`, or the one `--config DIR` names. The start logs which one it uses.

```text
.loommux/
    config.py        settings and resolve_workspace, read once when the daemon starts
    startup/         *.py and *.ipy files every kernel runs before its first cell
```

`config.py` is an ordinary Python module whose plain names are the settings:

| Name | Default |
| --- | ---: |
| `lease_mode` | `"activity"` |
| `private_activity_timeout_seconds` | `4800` |
| `named_activity_timeout_seconds` | `86400` |
| `heartbeat_interval_seconds` | `15` |
| `heartbeat_timeout_seconds` | `60` |
| `connection_grace_seconds` | `60` |
| `sweep_interval_seconds` | `10` |
| `orphan_grace_seconds` | `30` |

It may import modules and define helpers. Any other public name stops the start
with `config_unknown_setting`, so a misspelled setting is reported rather than
left at its default. A relative `DIR` is read from the directory loommux starts
in. The environment does not configure loommux.

The files of `startup/` run in name order in every kernel process: when a main
or child kernel opens, after `restart`, and after the replacement that stands
for an interrupt on Windows. They run as loommux's own requests with
`store_history=False`, so they take no execution number, and in the user
namespace, so what they define is there for the first cell; an `.ipy` file may
use magics such as `%run`. A file that succeeds is silent. One that raises, or
runs past 60 seconds and is interrupted, becomes a notice,
`Startup · NAME failed: …`, and the next file still runs. The directory is read
at each start, so a changed file applies to the next kernel. It is the place
for environment, things that work only if nobody has to remember to start
them; tools are one line to load when needed. See [Kernel Environment
Design](docs/kernel-environment-design.md), and the [tutorial](docs/kernel-environment-tutorial.md)
that builds a small environment step by step.

## Execution Model

Each accepted `run_cell` submission creates an execution record with one
public identity:

```text
execution: positive integer
```

The sequence begins at `1` for a newly provisioned logical resource and
increases only when a cell is accepted. Loommux accepts one running cell at a
time inside each resource; separate resources may execute concurrently. A
second `run_cell` call against the same busy resource is rejected with
`status="busy"`; it is not queued.

An execution can be `running`, `completed`, `error`, `interrupted`, or
`killed`. Python errors are recorded execution states, not MCP transport
failures. The error summary identifies the exception while the collected
traceback remains available from the execution's `traceback` stream.

The integer is the number IPython gives the same cell: `In [5]` in a result is
`In[5]` and `_i5` inside the kernel. `restart` starts the count again, as a
notebook does; the records of the replaced process go with it, and what an
agent saw of them is in its own transcript. When a cell yields a `text/plain`
display result, the combined log carries the same coordinate:

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
| `run_cell(freeform)` | Submit one loommux IPython cell to the persistent kernel and wait for its initial result. |
| `status()` | Inspect the main kernel's name, the workspace, its authored source category, interpreter, kernel PID, busy state, and current or recent execution. |
| `execution_status(execution=None)` | Inspect lifecycle and diagnostic metadata without returning the full output body. |
| `read_output(...)` | Read a selected execution stream, optionally by line range and with per-line clipping. |
| `search_output(...)` | Search a selected output stream using literal text or regular expressions. |
| `wait(execution=None, timeout_seconds=30)` | Wait for an execution without interrupting it. |
| `interrupt()` | Send an interrupt signal to the current running execution. |
| `restart()` | Restart the kernel. |

### Submitting A Cell

`run_cell` accepts one `freeform` loommux IPython cell and submits it exactly
as written. Its variables, imports, and other namespace state are available to
later cells in the same selected logical resource.

```python
import math
radius = 3
math.pi * radius**2
```

### The Cell Header

A cell that needs its own control begins with a `%%loommux` header, written in
ordinary IPython cell-magic syntax:

```python
%%loommux --wait 120
build_report()
```

The default initial wait for one MCP call is 10 seconds. `--wait SECONDS` only
changes how long that `run_cell` call waits: it does not limit Python runtime,
interrupt the cell when time expires, modify later calls, or add a variable to
the kernel. `--full-output` is described under
[Requesting Complete Output](#requesting-complete-output). The options apply to
that one cell; the next cell starts from the defaults again.

The header is IPython, not transport metadata. The daemon reads its options
and submits the cell unchanged, and the kernel interprets the header itself, so
another cell magic may follow and IPython history keeps the header:

```python
%%loommux --wait 0.5
%%bash
uv run pytest
```

A malformed header returns `invalid_cell_header` before an execution is
allocated or source is submitted. A `%%loommux` line anywhere but first raises
a `UsageError` saying so. See [Cell Header Design](docs/cell-header-design.md)
for the grammar and its reasons.

`--kernel NAME` in the header hands the cell to that kernel:

```python
%%loommux --kernel build --wait 120
%%bash
uv run pytest
```

The kernel named `build` is this home's child kernel, opened on first use and
retired with the main kernel (see [Controlling Kernels From A Kernel](#controlling-kernels-from-a-kernel)).
Its combined output is relayed into this cell as it arrives; an error there
ends this cell with a one-line `ForwardedCellError` beneath the relayed
traceback, and interrupting this cell interrupts it as well. Writing the
receiving kernel's own name is the same as writing none. Relayed output is
text; rich displays such as images stay in the target's execution record.

### Naming And Switching The Main Kernel

Each MCP session works in one main kernel. `--attach NAME --create` names it:
the session works in the main kernel `NAME` from this cell on, which is opened
when it does not exist and found by name when it does, whoever else works in
it. A phrase describing the task makes a good name, and the same first cell
returns to the kernel after the host was closed. Naming is what keeps a kernel
after its host closes:

```python
%%loommux --attach report-cleanup --create
import pandas as pd
```

Without `--attach`, a session's first call opens `private-<session>`, and
`--kernel` always forwards as described above. `--attach NAME` alone moves the
session to an existing main kernel at any time, for example after a first cell
that ran before the agent remembered its kernel, and releases its lease on the
one it leaves:

```python
%%loommux --attach report-cleanup
df.head()
```

Without `--create` a missing name returns `kernel_not_found`, listing the main
kernels, and opens nothing. The call that changes the session's main kernel ends
with one block such as `Kernel: report-cleanup (attached; 12 earlier
executions)`, and `status` begins with the kernel's name. A kernel renames
itself with `loommux.self.rename("new-name")`. The rules, and the terminal
multiplexers and notebook servers they come from, are in
[Main Kernel Binding Design](docs/kernel-binding-design.md).

When the call returns while the cell is still running, use `wait`,
`execution_status`, `read_output`, `search_output`,
`interrupt`, or `restart` to continue observing or controlling the
same execution.

### Output Streams And Long Output

Each execution retains five append-only text projections:

| Stream | Contents |
| --- | --- |
| `combined` | stdout, stderr, display results, and tracebacks in IOPub arrival order. |
| `stdout` | Python stdout stream events, and help IPython sends to a frontend's pager (`obj?`, `obj??`, `%%loommux?`), placed after the cell's other output. |
| `stderr` | Python stderr stream events. |
| `result` | `text/plain` from IPython execute-result and display-data events. |
| `traceback` | Traceback text from Python error events. |

Completed combined output of at most 5,000 `o200k_base` tokens is returned by
`run_cell` and `wait` beneath an `In [execution]:` header. A display result then
keeps its IPython-style `Out[execution]:` line; a silent cell returns only the
input header, and stdout or traceback remains in its original combined order.
For an execution that is still running, or for an unmarked terminal execution
whose combined output exceeds 5,000 tokens, the response retains the record but
omits the full body. Its omission notice reports the combined output's total
lines, Unicode code point characters, and UTF-8 size using one binary unit (`B`,
`KiB`, `MiB`, and so on). The structured `run_cell`, `wait`, and
`execution_status` surfaces expose the corresponding exact counts as
`output_total_lines`, `output_total_characters`, and
`output_total_utf8_bytes`. The output is not discarded; the notice itself names
the recovery paths, and the caller reads or searches the retained text through
`read_output` and `search_output`, or requests the complete body with the
`%%loommux --full-output` header. Token counting is required for this
automatic delivery policy; if the `o200k_base` tokenizer cannot be loaded, the
call fails instead of silently changing to another limit.

`read_output` uses `start:stop` inclusive line coordinates. Positive
endpoints are 1-indexed, endpoints may be omitted, and negative endpoints
count from the end of the selected stream:

```text
:10    first 10 lines
-10:   final 10 lines
20:40  lines 20 through 40
3:3    only line 3
```

When the caller has determined that the selected stream must be consumed in
full, omit `line_range`. `read_output` returns all of its lines in one
response, so there is no need to divide the read into consecutive small ranges.

`max_chars` clips each returned line without changing stored text or line
coordinates. `search_output` supports `literal`, `regex`, and `auto`
matching. In `auto` mode, loommux treats the query as a regular expression
when it compiles and falls back to literal matching when it does not. Search
results preserve original line numbers, mark matching lines with `M`, and
mark selected context lines with `C`.

### Requesting Complete Output

When a cell's entire terminal combined output is the intended result, include
`--full-output` in its header:

```python
%%loommux --full-output
build_report()
```

The option applies only to that execution. Once the execution is terminal, it
bypasses the normal 5,000-token delivery threshold and makes `run_cell` or a
later `wait` return the complete collected `combined` output. It does not cause
partial running output to be returned and does not alter the input or behavior
of `read_output` and `search_output`.

The full-output and wait options are independent and may be combined:

```python
%%loommux --wait 120 --full-output
build_report()
```

## Interrupting And Resetting

`interrupt` requests an interrupt for the current running cell. An
`interrupt_sent` response only confirms signal delivery; the execution reaches
its final state after the kernel reports IOPub `idle`.

`restart` is stronger: it stops the existing kernel and starts a
replacement in the same workspace with the same interpreter, which runs the
startup files again. A running cell is stopped. The execution records go with
the old process and the next cell is `In [1]`, as IPython numbers it.

Stopping the loommux server retires all resources. Their kernels, namespaces,
execution-record tables, output streams, and sequences are not persisted to
disk. Recycling one resource has the same persistence boundary for that
resource; a subsequently provisioned resource begins a fresh sequence at `1`.

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

```text
loommux/
    session.py
        Owns one persistent IPython namespace, execution identity, selection,
        waiting, control operations, execution records, startup files, and
        the notices not yet delivered.
    kernel/
        launch.py
            Builds the interpreter command, child environment, and private root.
        runtime.py
            Owns Jupyter kernel process lifecycle and platform containment.
        session.py
            Correlates IOPub messages with the active execution record.
    execution/
        record.py
            Stores one execution's lifecycle facts and normalized projections.
        events.py
            Names visible text, image, and delivery-failure events.
        logs.py
            Provides append-only streams, line ranges, clipping, and search.
        terminal.py
            Removes terminal controls while preserving chunk boundaries.
    cell_header.py
        Parses the %%loommux cell header for the daemon and the kernel.
    config.py
        Reads the --config directory: settings, resolve_workspace, startup/.
    workspace_resolver.py
        Resolves the kernel workspace before tools are exposed.
    notice.py
        The notice channel's MIME type and bounds, shared by kernel and daemon.
    kernel_extension.py
        IPython extension every kernel loads: interprets the cell header and
        places the daemon client in the namespace as ``loommux``.
    daemon.py
        HTTP client of the daemon's control plane.
    identity.py
        Names the kernel identity variables the daemon passes to its kernels.
    mcp/
        factory.py
            Registers MCP tools that consume the protocol-neutral session.
        result.py
            Projects session facts into MCP text, structured, and image content.
        presentation.py
            Renders MCP-facing model-readable text.
        entrypoints.py and server.py
            Select MCP transport and expose the installed command.
```

The top-level `loommux` package imports only the protocol-neutral Python
session API. FastMCP is entered explicitly through `loommux.mcp`; the runtime
does not depend on its transport consumer.

The current public contract is documented in [Coding Agent Control Plane
Design](docs/coding-agent-control-plane-design.md).
Focused references cover the [cell header](docs/cell-header-design.md),
[main kernel binding](docs/kernel-binding-design.md),
[kernel environment](docs/kernel-environment-design.md),
[freeform cell input](docs/ipython-mcp-freeform-run-cell-design.md),
[complete-output control](docs/ipython-mcp-full-output-directive-design.md),
[workspace configuration](docs/workspace-configuration.md), and the
[changelog](CHANGELOG.md).

## Development And Release Checks

Run the Python checks from the repository root:

```bash
uv run pytest
uv run ruff check src tests examples scripts
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
