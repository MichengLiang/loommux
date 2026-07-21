# Historical Design Notice

> Superseded by [Coding Agent Control Plane Design](coding-agent-control-plane-design.md). This retained proposal predates the explicit workspace resolver, `KernelLaunch` bootstrap, and terminal-text authority; its `workspace_config.py` and implicit `loommux_workspace.py` behavior is not current.

# IPython MCP Execution Control Plane Design

## 1. Purpose

This document defines the execution control plane of the loommux IPython MCP
server. It is the implementation contract for the persistent session, the
public execution coordinate, output reading, result presentation, the two MCP
result-channel entrypoints, and the monitor observation surface.

The intended reader is an engineer who has the repository source but does not
have any prior discussion context. The document defines the public objects and
their rules before it assigns implementation work.

## 2. Problem Domain

loommux is an MCP server process with one persistent IPython kernel. A user
works in that process for an interactive session, commonly a morning,
afternoon, or evening. The server accepts Python cells, preserves the kernel
namespace between cells, exposes execution output incrementally, and permits
later inspection of an execution.

The server process is the session lifetime boundary. Execution records and
their in-memory output exist while that process exists. A server process
restart starts a new session and assigns its first execution the number `1`.
loommux does not persist execution records, output logs, sequence counters, or
kernel state to disk. Long-term storage belongs to the user's surrounding
workflow rather than to this MCP server.

`reset_python` has a narrower boundary. It terminates and recreates the
IPython kernel but does not end the loommux server process. Records created
before reset remain available, and the next execution number continues the
existing session sequence.

## 3. Core Object Model

### 3.1 Execution Record

An **execution record** is one accepted `run_python` submission. It has one
public identity:

```text
execution: positive integer
```

The number is allocated by loommux before the cell is submitted to the kernel.
It is strictly increasing during a server process lifetime. The fifth accepted
submission has `execution == 5`, regardless of whether it prints a result,
raises an exception, runs asynchronously, or is later interrupted.

The record owns the following state:

| Property | Meaning |
| --- | --- |
| `execution` | Public session-local execution coordinate. |
| `code` | Original freeform Python cell submitted to the kernel. |
| `status` | `running`, `completed`, `error`, `interrupted`, or `killed`. |
| timestamps | Submission, latest update, and terminal completion times. |
| kernel metadata | Kernel PID and the kernel-local count observed at submission. |
| logs | Append-only combined, stdout, stderr, result, and traceback texts. |
| error summary | Exception name and exception value when Python reports an error. |

The kernel-local execution count is diagnostic metadata. It does not identify a
loommux execution record. IPython resets that count when a kernel restarts and
does not display it for cells without a display result. These properties make
it unsuitable as a public resource coordinate.

### 3.2 Public Sequence and IPython Presentation

Every accepted execution result begins with an IPython input-history header
using the loommux session coordinate:

```text
In [5]:
```

The MCP tool call already carries the submitted source, so this header is a
compact projection of the accepted cell rather than a second rendering of its
code. It gives stdout-only, silent, errored, interrupted, and killed cells the
same visible session coordinate without claiming that they produced a display
result.

The loommux execution number authors the `Out[n]` label only in combined
output for an `execute_result` or a `display_data` event with `text/plain`.
If execution `5` produces such a result, its complete model content is:

```text
In [5]:
Out[5]: <text/plain>
```

This is an IPython-style reading surface whose number is stable for the full
loommux session. It is not a forwarding of the kernel's resettable display
counter. After a kernel reset, the first result from the replacement kernel can
therefore render as `In [5]:` followed by `Out[5]` when it belongs to loommux
execution `5`.

The server may retain the kernel's actual count for diagnostics and monitor
correlation. It must not use that count to address a record or to author the
public `Out[n]` label.

### 3.3 Output Streams

Each execution record has five output streams:

| Stream | Contents |
| --- | --- |
| `combined` | stdout, stderr, display result, and traceback in IOPub arrival order. |
| `stdout` | Text from stdout stream events. |
| `stderr` | Text from stderr stream events. |
| `result` | `text/plain` from execute-result and display-data events. |
| `traceback` | Traceback text from Python error events. |

`stream` is a projection selector for one execution record. It is not a second
resource identifier and it is not encoded into a resource path.

## 4. Lifecycle

### 4.1 Server Startup

At MCP lifespan start, loommux resolves the workspace and interpreter through
`workspace_config.py`, confirms that the interpreter imports `ipykernel`, and
starts one kernel. The server process cwd remains the workspace discovery
origin. The project `loommux_workspace.py` may select a Codex workspace root.

There is no public workspace-setting tool. Workspace and interpreter selection
are server startup configuration, while `python_status` is their observable
projection. Tool lists, tool descriptions, examples, tests, and monitor
documentation must not describe a nonexistent workspace-setting operation.

### 4.2 Submission

`run_python(freeform)` accepts one loommux Python cell. The adapter prepares
protected multiline raw strings according to
[IPython MCP Protected Multiline Raw String Design](ipython-mcp-protected-multiline-string-design.md),
rejects a new submission while another record is `running`, allocates the next
execution number, saves the record, and submits the prepared Python source to
the kernel. A busy kernel returns `status="busy"` and the current execution
number. It never queues a second cell.

The timeout directive remains a run-python input rule:

```python
# loommux: timeout_seconds=120
```

Exactly one complete directive line selects the wait duration for that tool
call. No valid single directive means a 10-second wait duration. The directive
does not limit Python runtime, change later calls, interrupt the cell, or add
runtime variables.

### 4.3 Completion and Reset

A terminal IOPub idle event completes a running record. A Python error creates
an `error` record without crashing the kernel. `interrupt_python` signals the
current kernel execution; its final state is reported when IOPub reaches idle.
`reset_python` marks a still-running record `killed`, stops the old kernel,
starts a replacement kernel, and preserves the adapter's records and next
execution number.

The following example is normative:

1. Executions `1`, `2`, `3`, and `4` run in one server process.
2. `reset_python()` restarts the kernel after execution `4`.
3. The next accepted cell has `execution == 5`.
4. If that cell returns a display result, its combined output renders
   `Out[5]: ...`.
5. Records `1` through `4` remain readable by their integer execution number.

## 5. Public MCP Tool Contract

The server exposes the following eight tools. Both result-channel entrypoints
expose the same names and input schemas.

```text
run_python(freeform)
python_status()
python_execution_status(execution: int | null = null)
read_python_output(execution: int | null = null, stream="combined",
                   line_range=null, show_line_numbers=false, max_chars=null)
search_python_output(query, execution: int | null = null, stream="combined",
                     query_mode="auto", context_before=0, context_after=0,
                     ignore_case=false, max_chars=null)
wait_python(execution: int | null = null, timeout_seconds=30)
interrupt_python()
reset_python()
```

### 5.1 Execution Selection

`python_execution_status`, `read_python_output`, `search_python_output`, and
`wait_python` share one selection rule:

1. A supplied `execution` selects that exact positive integer record.
2. An omitted `execution` selects the current running record.
3. If no record is running, an omitted `execution` selects the most recently
   submitted record.
4. If neither record exists, the tool returns `execution_not_found`.

The public input has one source of execution identity. No alternate log handle
or string id participates in selection. This rule prevents one input field
from silently overriding another selection field.

### 5.2 `run_python`

`run_python` returns the submitted execution record after waiting for the
selected wait duration. Its structured result always includes `execution` on
an accepted submission. That is true for completed, running, error,
interrupted, killed, and line-limited output states.

Small completed output is returned in the model content surface. Running and
line-limited output omit the full body from that response while retaining the
record for later reading. The line limit protects one MCP response from an
unbounded output body; it does not delete the in-memory record log.

### 5.3 `python_execution_status`

This tool returns state and metadata for the selected execution record. It does
not return full output text. Its structured result includes the execution
number, status, timestamps, kernel metadata, output line total, omission
reason when applicable, and error summary when applicable.

### 5.4 `read_python_output`

This tool reads text from the selected record and stream. `line_range` uses
the authored surface `start:stop`. Positive endpoints are 1-indexed line
numbers. Endpoints may be omitted. Negative endpoints count from the end of
the selected stream. `stop` is inclusive.

Examples:

```text
:10    first ten lines
-10:   final ten lines
20:40  lines 20 through 40
3:3    line 3
```

`show_line_numbers=true` prefixes returned lines with their 1-indexed stream
line number. `max_chars` limits each returned line independently and does not
change the stored text or line coordinate. A non-positive value is invalid.

### 5.5 `search_python_output`

This tool searches one selected execution stream. `query_mode="literal"`
matches literal text, `query_mode="regex"` requires a valid regular
expression, and `query_mode="auto"` first interprets the query as regex and
uses literal matching only when compilation fails. `context_before` and
`context_after` add neighboring stream lines to each match. Search output
marks matching lines and context lines while preserving original line numbers.

### 5.6 `wait_python`, `interrupt_python`, and `reset_python`

`wait_python` waits at most `timeout_seconds` for the selected record and then
returns that record's current state. It does not interrupt on timeout.

`interrupt_python` signals the current running record. It returns its
execution number when a signal is sent. An idle kernel returns an idle result.

`reset_python` restarts the kernel described in Section 4.3. It does not clear
the loommux execution table, output streams, or session sequence.

## 6. Result Surfaces

### 6.1 Structured Result

The standard result channel supplies a structured state object. The public
execution field is named `execution` and has integer type. Status payloads do
not expose private log address strings. Output stream selection occurs at
`read_python_output` and `search_python_output` through `execution` plus
`stream`.

An accepted completed result can have this shape:

```json
{
  "ok": true,
  "execution": 5,
  "status": "completed",
  "stdout": "",
  "stderr": "",
  "result_text": "42",
  "error": null,
  "output_omitted": false,
  "output_omitted_reason": null,
  "output_line_limit": 300,
  "output_total_lines": 1,
  "kernel": {"busy": false, "kernel_pid": 12345}
}
```

An execution error remains an execution state, rather than an MCP transport
failure. It includes `execution`, `status: "error"`, and an error summary.
The traceback remains readable through the same execution number with
`stream="traceback"`.

### 6.2 Model Content

Model content presents Python behavior and lifecycle state. It does not append
private protocol markers or serialized state dictionaries. Every accepted
`run_python` or `wait_python` result begins with `In [execution]:`, which is
the public session coordinate for the submitted cell.

When a result stream exists, the header is followed by combined output's
result line with the loommux session number:

```text
In [5]:
Out[5]: 42
```

When a successful cell does not create a display result, it still returns the
input header and does not invent an `Out[n]` line:

```text
In [5]:
```

For stdout-only success, collected visible output follows the header. For an
error, the collected traceback likewise follows the header. The exact ordering
is specified in `presentation.py`; the number remains visible without relying
on stdout, a display result, or an exception body.

Running and line-limited executions use concise control text below the same
header because their full combined body is unavailable. A killed execution
retains an explicit killed-state line because prior output cannot prove that it
completed normally. Examples:

```text
In [5]:
Running: use wait_python() or read_python_output().

In [5]:
Output: more than 300 lines; use read_python_output().
```

`read_python_output` returns the selected log text. Empty reads return the
documented empty-result sentence. `search_python_output` returns matched lines
and context; a zero-match search returns its documented zero-match sentence.
These reader contents remain output-focused because the caller already chose
the execution.

### 6.3 Tool Descriptions

Tool descriptions define the current action, inputs, interpretation rules, and
required next action. They use the public integer execution coordinate and the
stream selector. They do not teach discarded resource-address grammar, old
parameter names, or compatibility alternatives.

The following facts belong in descriptions because they change a model action:

| Tool | Required description facts |
| --- | --- |
| `run_python` | Raw Python cell input, timeout directive, 10-second default, session execution number, and follow-up actions for running or large output. |
| `python_execution_status` | Integer execution selection and current-or-last default. |
| `read_python_output` | Integer execution selection, stream values, line range coordinate, line numbering, and per-line clipping. |
| `search_python_output` | Integer execution selection, stream values, query modes, context, case behavior, and per-line clipping. |
| `wait_python` | Integer execution selection, current-or-last default, and wait duration. |
| `python_status` | Kernel state and current/recent execution observation. |
| `interrupt_python` | Current execution signal behavior. |
| `reset_python` | Kernel restart and session-sequence preservation. |

## 7. Result-Channel Entry Points

loommux has two MCP result-channel policies:

| Entry point policy | `content` | `structuredContent` |
| --- | --- | --- |
| `dual_channel` | Model presentation text. | Full public structured status. |
| `content_only` | The same model presentation text. | Absent. |

The policies have identical runtime behavior, tool names, input schemas,
docstrings, selection rules, lifecycle rules, and presentation source. They
differ only in the presence of structured content. A shared server factory
owns tool registration, lifespan setup, adapter construction, monitor call
wrapping, and descriptions. Entry modules select a policy and transport
configuration without duplicating tool bodies.

The content-only policy receives the same `In [n]:` header as the dual-channel
policy. It therefore retains the session coordinate even when the cell has no
authored `Out[n]` line and cannot rely on a structured field.

Transport is a separate configuration concern. If the two entrypoints require
different transports, the configuration documentation names that difference
explicitly. A result-channel policy alone does not imply a transport choice.

### 7.1 Entrypoint Transport Configuration

`mcp_ipython_server.py` is the standard dual-channel stdio entrypoint. It
starts through `mcp.run()` and is configured by an MCP host that launches a
subprocess.

`mcp_ipython_content_server.py` is the content-only streamable-HTTP entrypoint.
It starts through `mcp.run(transport="streamable-http", host="0.0.0.0",
port=8801)`. A client that uses this entrypoint connects to that HTTP server
instead of starting a stdio subprocess. This transport distinction is a
deployment configuration fact; it does not alter execution identity, tool
schemas, result presentation, or result-channel policy semantics.

## 8. Monitor Contract

The monitor is an observation side path. Its failure, absence, queue overflow,
or network unavailability does not alter an MCP tool result, kernel lifecycle,
or execution record.

Monitor events use the same integer coordinate as MCP tools:

| Event | Required execution fields |
| --- | --- |
| `execution_submitted` | `execution`, call id, workspace, kernel PID, code, timeout, timestamp. |
| `execution_output` | `execution`, stream, text, kernel diagnostic count if needed, timestamp. |
| `execution_finished` | `execution`, terminal status, line total, error summary, timestamp. |

The monitor UI aggregates records by the numeric `execution` field and shows
the same sequence visible in the main MCP control plane. It may display code,
streams, duration, status, error summary, workspace, and output count. It does
not require a separate output-address string to render or select a record.
When a monitor receives a result stream event, its combined-output projection
authors `Out[execution]:` with the same multiline rule used by the execution
record. The result stream tab remains the raw `text/plain` stream.

## 9. Implementation Responsibilities

### 9.1 Runtime Core

`src/loommux/execution.py` defines the integer execution record, its snapshots,
terminal transitions, and output authoring inputs. `src/loommux/output_log.py`
retains line storage, line-range resolution, clipping, and search. It does not
export public log-handle construction or handle parsing.

`src/loommux/adapter.py` allocates the sequence, owns
`dict[int, Execution]`, applies the shared selection rule, and maps
`execution + stream` to a `LineLog`. It owns no text presentation. It does not
accept alternate resource locators.

`src/loommux/kernel_session.py` continues to collect IOPub messages. On a
result or display event it supplies the execution record to output authoring;
the record's public execution number determines `Out[n]`.

### 9.2 Presentation and MCP Binding

`src/loommux/presentation.py` authors all model content described in Section
6. It receives public state objects and does not start kernels, choose
executions, parse input, or mutate records.

`src/loommux/mcp_result_policy.py` remains responsible only for transforming a
presentation string and raw status into the selected result channel policy.

The current duplicated server modules are replaced or reduced through one
shared factory module. `mcp_ipython_server.py` and
`mcp_ipython_content_server.py` remain stable entrypoint names if deployment
configuration uses them, but their substantive tool definitions live in one
place.

### 9.3 Monitoring

`src/loommux/monitoring.py` publishes the integer execution coordinate. The
monitor TypeScript event definitions, reducer, application labels, unit tests,
and browser E2E fixtures use the same field and type.

## 10. Documentation Boundary

The current documentation set must have one authority for each subject:

| Subject | Authority |
| --- | --- |
| Execution identity, lifecycle, output streams, selection, result surfaces | This document. |
| Freeform timeout directive grammar | `ipython-mcp-freeform-run-python-design.md`, which references this document for execution behavior. |
| Full-output directive grammar and delivery behavior | `ipython-mcp-full-output-directive-design.md`, which references this document for execution behavior. |
| Result-channel and entrypoint policy | This document or a focused server-channel document that references it. |
| Monitor event and UI behavior | Monitor documentation that references this document for the execution field. |

Documents that define removed resource-address fields are historical material,
not current interface specifications. Their status must be made explicit or
their duplicated rules removed. Current documents must not contradict the eight
tool schemas.

## 11. Migration Rules

The implementation is an internal contract replacement. It does not preserve
public aliases for retired string identifiers or output-address fields. Tests,
examples, monitor fixtures, and current documentation move to the integer
execution contract in the same change set. This avoids retaining multiple
resource languages in a project without external compatibility obligations.

The original raw material remains unchanged. It records observations that led
to the design and is not an executable or public tool contract.

## 12. Verification Requirements

Verification must exercise the actual MCP boundary as well as unit-level line
log behavior.

### 12.1 Execution Identity

1. The first three accepted submissions return executions `1`, `2`, and `3`.
2. `read_python_output(execution=2)` reads only record `2`.
3. `wait_python(execution=2)` and `python_execution_status(execution=2)`
   select record `2`.
4. Omitting execution selects current, then most recent, then reports
   `execution_not_found` when no record exists.
5. A busy submission reports the running integer execution and does not queue.
6. The tool schemas expose `execution` and do not expose alternative resource
   selector fields.

### 12.2 Output Presentation

1. Every accepted execution result begins with `In [execution]:`.
2. A display result from execution `5` then renders its authored `Out[5]:`
   line.
3. A successful assignment returns only `In [execution]:`, with no completion
   sentence or synthetic `Out` line.
4. A stdout-only success has its header before stdout in the documented
   content ordering.
5. Running, line-limited, error, interrupted, and killed results expose their
   execution number through the same header.
6. Stream reads and searches preserve their text, line-range, search, context,
   and clipping semantics.

### 12.3 Reset and Session Scope

1. Reset kills a running record when necessary and starts a new kernel.
2. Records created before reset remain readable.
3. The first result after reset uses the next loommux execution number in its
   `Out[n]` label rather than kernel-local `Out[1]`.
4. A fresh server process begins its sequence at `1`.

### 12.4 Dual-Channel and Monitor Coverage

1. The dual-channel server returns identical presentation text plus structured
   status.
2. The content-only server returns identical presentation text without
   structured content.
3. Both server entrypoints list the same eight tools with the same input
   schemas and descriptions.
4. Monitor events and UI aggregation use integer execution values.
5. Monitor delivery failure does not affect any MCP operation.

### 12.5 Final Commands

Run the Python suite from the loommux project:

```bash
uv run pytest
```

Run monitor verification from `monitor/`:

```bash
pnpm test
pnpm typecheck
pnpm e2e
```

The final implementation is complete when the real MCP tool list, tool
descriptions, schemas, standard result channel, content-only result channel,
runtime behavior, monitor UI, tests, and current documents all describe and
use the same integer execution control plane.
