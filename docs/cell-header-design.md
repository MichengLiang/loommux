# Loommux Cell Header And In-Kernel Control Design

> **Status: Implemented on October 21, 2026.** This document supersedes the
> July 24, 2026 *Cell Control Directive Design* and its *Transient Control
> Directive Consumption* rationale. Section 9 records which of their decisions
> are reversed and why.

## 1. Decision

A Loommux cell may begin with one header line written in ordinary IPython
cell-magic syntax:

```text
%%loommux [--kernel NAME | --attach NAME [--create]] [--wait SECONDS] [--full-output]
```

The header declares **which kernel interprets the rest of the cell**, in the
same sense that `%%bash` declares that Bash interprets it. Omitting the header,
or omitting `--kernel`, means the kernel that received the cell. Its options
declare how the submitting call observes this one cell: how long `run_cell`
waits, and whether a terminal response carries the complete combined output.

`--attach NAME` is the one option about the session rather than the cell: from
this cell on, the submitting MCP session works in the main kernel `NAME`, and
`--create` opens it when it does not exist. The
[Main Kernel Binding Design](kernel-binding-design.md) owns what that means;
this document keeps its grammar and how each reader treats it.

The header is legal IPython. Loommux does not remove it before submission; the
kernel interprets it, and IPython history keeps it.

Loommux is the daemon that owns every kernel. Each kernel therefore also
receives a `loommux` object, a client of that daemon, through which code in any
kernel can list, address, run, interrupt, restart, and recycle kernels.

The interpreters form a tree, and every layer reads only its next hop:

```text
loommux daemon
└── kernel (IPython)      %%loommux --kernel build   → kernel "build"
    └── engine            %%bash               → bash
```

## 2. Journeys

The design is derived from what the author of a cell is doing. Each journey
names the only rule it needs.

| Journey | Cell | Rule needed |
| --- | --- | --- |
| Ordinary work | `df.describe()` | none |
| Wait longer, read everything | `%%loommux --wait 120 --full-output` then the code | the header is the first line |
| Run elsewhere | `%%loommux --kernel build --wait 60` then `%%bash` and the command | `--kernel` names the kernel |
| Name the session's main kernel, or return to it | first cell `%%loommux --attach 任务名 --create` | only the daemon reads `--attach` |
| Move the session to another main kernel | `%%loommux --attach 任务名` | `--attach` works on any call |
| Manage kernels | `loommux.kernels()`, `loommux["build"].restart()` | `loommux` is a Python object |
| Background work in another kernel | `job = loommux["build"].run(code)` | `run` returns a job handle |

"This kernel" is a design concept, not an author-facing word. There is no
reserved kernel name for it: an author who writes the receiving kernel's own
name gets exactly the omitted-name behavior.

Control stays per cell. There is no session-level default: a header controls
its own cell, and the next cell starts from the defaults again. `--attach`
changes no default either; it moves the session, as `cd` moves a shell, and
what a later header means does not depend on it.

## 3. Grammar

The header is the first line of the cell after leading blank lines, the same
line IPython itself examines for a cell magic. It is recognized when it is
`%%loommux` followed by whitespace or the end of the line, so `%%loommux?`
remains IPython's help request for the magic.

The rest of the line is split with POSIX shell-word rules and parsed by one
`argparse` parser defined in `loommux.cell_header`:

- `--kernel NAME`: the kernel that interprets the cell, printable, at most
  256 characters (the same rule as `X-Loommux-Resource`);
- `--attach NAME`: the main kernel the submitting session works in from this
  cell on, by the same rule; it cannot appear together with `--kernel`,
  because both name where the cell runs;
- `--create`: a flag, valid only with `--attach`;
- `--wait SECONDS`: a positive finite number of seconds;
- `--full-output`: a flag.

Every other token is an error, and option names are never abbreviated, so
a word without dashes, such as `full-output`, or a prefix such as `--wai`, is
rejected rather than read as something else. An invalid header in a `run_cell` submission
returns `invalid_cell_header` before an execution number is allocated or source
reaches a kernel.

## 4. One Grammar, Two Readers

The header line serves two readers, and each takes its own part:

| Reader | Reads | Why it must be this reader |
| --- | --- | --- |
| Daemon, in `run_cell` | `--wait`, `--full-output`, validity | They describe the MCP call, which the daemon answers before the kernel finishes |
| Daemon, in `run_cell` | `--attach`, `--create` | They choose the kernel the cell is submitted to, which is decided before any kernel sees it |
| Kernel extension | `--kernel` | Only the interpreting kernel can route the body onward |

Both readers import the same parser, so they cannot disagree about the
grammar. The daemon reads the first line and submits the cell unchanged. When
the header names another kernel, its options still describe the submitting
call: forwarded output is relayed into this cell (section 7), so `--wait` and
`--full-output` mean exactly what they mean for a local cell.

## 5. Interpretation Inside The Kernel

The extension `loommux.kernel_extension` is loaded by every Loommux kernel at
start through `--InteractiveShellApp.extensions`.

### 5.1 A header naming this kernel is an input transformation

The extension appends a pure function to `input_transformers_cleanup`. When
the first line is a header naming this kernel, it removes that line. IPython
then transforms and runs the body as the cell itself: a following `%%bash`
becomes the cell magic, top-level `await` runs on the kernel's own loop, and
the last expression is displayed as `Out[n]`.

A cell has one header. When the next line is another `%%loommux` header, the
transformer leaves both in place, and the `%%loommux` magic reports that the
options belong on the first line. Dissolving both would split the readers:
the daemon reads only the first line, so in `%%loommux` followed by
`%%loommux --wait 120` the wait would silently have no reader. A header
naming another kernel is not dissolved, so a header on its next line belongs
to the forwarded body and is read by the target.

This cannot be a cell magic. A cell magic is a synchronous function, so the
only way for it to run a body is a nested `run_cell`, and a nested `run_cell`
cannot run top-level `await` while the kernel's loop is already running the
outer cell. Measured in an ipykernel 7 kernel on October 21, 2026:

```text
%%nestprobe              # a cell magic calling get_ipython().run_cell(cell)
import asyncio
await asyncio.sleep(0)   # RuntimeError: This event loop is already running
```

The same body under a header removed by a cleanup transformer ran, awaited, and
displayed `Out: 42`. Coding agents write top-level `await` routinely, so the
transformer is the only form that keeps the header free of restrictions.

IPython may call transformers more than once for a cell (completeness checks
transform it too), which is why the function is pure.

`--attach` naming this kernel is dissolved the same way: by the time the cell
arrives, the daemon has already moved the session here.

### 5.2 A header naming another kernel is a cell magic

A header naming another kernel is left in place. IPython's own cell-magic
transformation turns it into `run_cell_magic("loommux", line, body)`, and the
registered `%%loommux` magic forwards the body (section 7). Its docstring is
the grammar, so `%%loommux?` documents the header from inside any kernel.

### 5.3 A misplaced header is named as such

IPython reads `%%loommux` on a later line as a line magic called `%loommux`.
The extension registers a line magic under that exact name that raises
`UsageError("%%loommux must be the first line of the cell")`. Lines before it
have already run, as with any failing line magic; the daemon does not scan
later lines, because a header-shaped line inside a string literal is data.

`--attach` naming another kernel reaches the cell magic only in a cell that no
session submitted, a forwarded one or one sent through the control plane, and
raises a `UsageError` saying that only the daemon reads it. IPython reports a
`UsageError` without failing the execution, so the message is the result.

## 6. Kernel Identity And The `loommux` Object

### 6.1 Identity

The resource manager assigns a kernel resource its id before it starts the
kernel process, and the launch environment carries:

| Variable | Value |
| --- | --- |
| `LOOMMUX_KERNEL_ID` | the resource id |
| `LOOMMUX_KERNEL_NAME` | its display name, such as `build` or `private-1a2b3c4d` |
| `LOOMMUX_CONTROL_URL` | the daemon's HTTP origin, when it serves one |

An HTTP daemon publishes its loopback origin; a wildcard bind address such as
`0.0.0.0` is published as `127.0.0.1`. A stdio daemon serves no HTTP control
plane, so its kernels have no `LOOMMUX_CONTROL_URL`, and every `loommux`
operation that needs the daemon raises an error saying so.

### 6.2 The client

`loommux.daemon.Daemon` is an HTTP client of the control plane. The extension
places one, bound to this kernel, in the user namespace as `loommux`.
Outside a kernel, `Daemon()` uses `LOOMMUX_CONTROL_URL` or
`http://127.0.0.1:8801`.

| Expression | Meaning |
| --- | --- |
| `loommux.kernels()` | the kernels of this home: its main kernel and child kernels, with name, id, state, scope, and home |
| `loommux["build"]` | handle of this home's child kernel `build`, opened on first use; this kernel's and its main kernel's own names resolve to them |
| `loommux.self` | handle of this kernel |
| `kernel.rename(name)` | rename a kernel and return the name it received; renaming this kernel changes which header names are its own from the next cell |
| `kernel.run(code, wait=10)` | submit `code`; returns a `Job` |
| `kernel.interrupt()`, `kernel.restart()`, `kernel.recycle(force=False)` | control operations |
| `job.wait(timeout)`, `job.status()`, `job.output(stream, lines)`, `job.follow(offset)`, `job.interrupt()` | follow one execution |
| `loommux.close()` | close the HTTP connections; the extension's unload calls it, and a later request reopens them |

The client sends requests with `trust_env=False`: the control plane is always
on loopback, and an inherited `ALL_PROXY` would route loopback traffic into a
proxy.

### 6.3 Why the control plane is a client, not a kernel inside the daemon

A kernel embedded in the daemon process could touch the resource manager
directly, but any blocking call or endless loop in it would stop the daemon's
event loop, which is the failure the October hang-fix rounds removed
(`kernel-resource-daemon-design.md` section 11). Threads cannot be killed or
cleanly interrupted. Across a process boundary, a mistake in a controlling
kernel stays in that kernel. Because the control plane is an object, every
kernel has it, and no special "daemon kernel" is needed.

## 7. Forwarding

`%%loommux --kernel build` in kernel A:

1. resolves `build` in A's home, opening a child kernel if none is registered
   (section 8);
2. submits the body unchanged to `build`;
3. relays `build`'s combined output into A's cell as it arrives, by character
   offset into the append-only combined log, until the execution ends; each
   follow request returns as soon as new text exists or the execution ends,
   and holds a daemon worker thread for at most one second. A request costs
   only the text it returns: it neither joins the whole log nor counts its
   lines. Doing either per request is quadratic in the output and holds the
   lock the target's output collector needs; end to end it added about 2.6 s
   (18 %) to relaying 32 MB, and a follower asking more often pays more;
4. ends A's cell in the same state: an error in `build` raises in A an error
   whose display is one line naming the kernel and the exception, because the
   traceback itself has already been relayed.

Consequences that the implementation must preserve:

- **No MCP change.** From the MCP tools' view, a forwarding cell is an ordinary
  cell of A. `wait`, `read_output`, and `search_output` work unchanged, and an
  expired `--wait` leaves the forwarding running.
- **Interrupts travel down.** Interrupting A delivers `KeyboardInterrupt` to the
  magic, which interrupts `build`, keeps relaying until `build` finishes or ten
  seconds pass, and then raises a `KeyboardInterrupt` that displays as one line.
  Its class name stays `KeyboardInterrupt`, which is how A's execution record
  recognizes an interrupted rather than a failed cell.
- **Cycles fail immediately.** If `build` forwards back to the busy A, the
  daemon answers `busy`; the inner magic raises, and the error reaches A.
  Nothing waits for itself.
- **Text only.** Relayed output is the combined text stream. Rich displays such
  as images stay in `build`'s execution record.
- **No provenance banner.** The author wrote the target name on the line above,
  and the relay never interleaves another kernel's output, so a label would only
  cost tokens.

## 8. Homes

A kernel opened by another kernel is a child kernel of the opener's main
kernel, its home, and lives as long as that main kernel holds a client lease
([Main Kernel Binding Design](kernel-binding-design.md) section 3.2). A child
kernel opening a kernel opens it in the same home. Names resolve inside the
home, so forwarding to `build` reaches this home's `build`, never another
session's. An unanchored child kernel follows the leaseless path: it is
recycled when idle, and orphaned with a grace period when work is still
running.

Snapshots name a resource's scope (`main` or `child`) and its home, and the
console shows them. Handles obtained outside any kernel resolve a main kernel
by name or any kernel by id, and open nothing; creation needs a home.

## 9. Reversed Decisions

| July 2026 decision | Status | Reason |
| --- | --- | --- |
| Control is a `# loommux:` comment | Reversed | Recognizing it required tokenizing Python to exclude string literals, a second classifier for cell-magic bodies, and the assumption that `#` starts a comment, which fails for every body language that does not use `#` |
| A Loommux cell magic would compete for IPython's single cell-magic slot | Reversed | IPython composes wrapper magics (`%%time` over `%%bash` works), and a header naming this kernel never reaches the slot at all: the transformer removes it before IPython looks |
| Directives are transport metadata that must never reach IPython or its history | Reversed | That held for comments, which were not IPython. The header is IPython with a real meaning and replays correctly, so history recording it is accurate |
| The adapter removes control lines by exact source ranges | Removed | Nothing is removed by the daemon |
| `invalid_loommux_directive` | Renamed | `invalid_cell_header` |
| Only the full-output flag survives submission, as private execution state | Kept | A later `wait` still needs it |
| Apply Patch programs in a triple-quoted Python literal are converted to a plain `str` before submission, the one exception to source fidelity | Removed | The `%%apply_patch` cell magic receives a patch verbatim, so a patch no longer needs to be a Python string. The conversion ignored the body's language and rewrote matching text inside `%%bash` heredocs and forwarded cells |

## 10. Rejected Alternatives

| Alternative | Reason |
| --- | --- |
| Daemon strips the header before submission | Makes the header transport metadata again and hides the routing from history |
| Interpret a header naming this kernel in the cell magic | Loses top-level `await` (section 5.1) |
| An IPython shell embedded in the daemon as its control kernel | Breaks event-loop discipline (section 6.3) |
| A session default such as `%loommux --wait 30` | Control is per cell; a default would make one cell's meaning depend on earlier cells |
| Per-line provenance labels on relayed output | Token cost with no ambiguity to resolve |
| A positional kernel name, `%%loommux build` | Every word without dashes became a forward: the slip `%%loommux full-output` would open a kernel named `full-output`. With options only, a bare word can only be an error. Adopted briefly and replaced the same day |
| A reserved name such as `self` | The author never needs it; the receiving kernel's own name already works |
| `--kernel` on a session's first call chooses its main kernel | Gave one option two meanings chosen by history; replaced the same day by `--attach NAME --create` ([Main Kernel Binding Design](kernel-binding-design.md) section 11) |

## 11. Verification

| ID | Proof |
| --- | --- |
| H1 | A cell without a header runs with a 10-second initial wait and normal delivery |
| H2 | `%%loommux --wait` and `--full-output` change only their own cell, through a later `wait` as well |
| H3 | An invalid header returns `invalid_cell_header`, allocates no execution, and reaches no kernel |
| H4 | In a real kernel, a header followed by `%%bash`, by top-level `await`, and by a final expression behaves as the body alone would |
| H5 | The header naming the kernel's own name behaves as the omitted name |
| H6 | A misplaced header raises the first-line `UsageError`; `%%loommux?` shows the grammar |
| H7 | IPython's raw input history keeps the header |
| H8 | `--attach` parses, cannot be combined with `--kernel`, dissolves when it names the receiving kernel, and is a `UsageError` otherwise |
| H9 | `--create` parses only with `--attach` |
| K1 | Kernels receive id, name, and control URL; a stdio daemon's kernels report that no control plane exists |
| K2 | `loommux.kernels()`, `loommux.self`, interrupt, restart, and recycle work from inside a kernel over HTTP |
| P1 | `loommux["build"].run()` opens a child kernel and its job waits, reports status, and reads output |
| P2 | A child kernel is released with its main kernel, including one opened from another child kernel |
| F1 | `%%loommux --kernel build` relays output, ends in the target's state, and shows a one-line error |
| F2 | Interrupting the forwarding cell interrupts the target |
| F3 | Forwarding back into the busy origin fails with `busy` instead of hanging |
| S1 | `scripts/stress.py` passes with header cells in its load mix |

Real-kernel tests prove H4 through H7, K, P, and F; an adapter-local string check
cannot show how IPython interprets a cell.
