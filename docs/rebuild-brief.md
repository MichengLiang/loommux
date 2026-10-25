# Rebuild Brief

> 2026-10-26, at `4983e47`. The inventory taken before the rebuild's design
> document is written: what the rebuild stands on, what it moves, what it must
> keep working, what it must grow, and what is still open. It replaces
> `tmp/async-hypermorph/HANDOFF.md`, which was written after round 9, was never
> committed, and does not know rounds 10 to 30.

This is not a design document. It fixes no new shape; where a destination is
not yet derivable it says *to be designed*. The design document starts from
here and cites the spikes and design documents this brief points to.

The rebuild, agreed on 2026-10-22: the daemon drives kernels through
jupyter_client's native async API (`AsyncKernelManager`, `AsyncKernelClient`),
writes its own concurrency with AnyIO on the asyncio backend, and represents
its domain facts (the directory of kernels, each kernel resource, each
execution, the lease policy) as Hypermorph live objects.

## 1. Status: features are frozen

**Decision (2026-10-25/26).** No new features until the rebuild. Bugs are
still fixed, and every fix comes with a behavior test, so the rule it
establishes moves into the new skeleton with the tests (section 4). The daemon
on port 8801 stays in daily use: using it is where rounds 10 to 30 found their
requirements, and that does not stop. Hora's change for background agents
(spike 03 §4.8) belongs to Hora and is not frozen.

**Why.** Every round from 10 to 30 was built on the synchronous bridge the
rebuild removes: tool calls run session operations through
`asyncio.to_thread`, one collector thread per kernel polls iopub, state is
guarded by `threading.RLock`, and `kernel/runtime.py` carries workarounds for
`jupyter_core.run_sync` (a private `contextvars.Context`,
`close_jupyter_event_loop`, liveness read from the process instead of
`KernelManager.is_alive`). Each new feature crossed that impedance mismatch
once more, and each crossing added a lock, a poll or a comment explaining a
detour. Three examples from the last two days:

- **loommux#002** (an interrupt sent before the kernel started the cell was
  lost, `docs/issues/closed/002-interrupt-right-after-submit.rst`) was fixed
  with three flags under the session lock in `kernel/session.py`. In an async
  adapter "this cell has started" is an event to await (spike 03 §4.7).
- **An abandoned `wait` keeps its server thread** until the cell ends: spike 01
  §3.3 measured the starvation this leads to (the 33rd waiter stalls every
  other call for about 55 s), and spike 03 §3.3 reproduced the held threads at
  HEAD.
- **Notices delivered while a cell runs** (round 29) and **`--silence`**
  (rounds 23, 24) are both further conditions in `session._await`, a loop that
  polls every 0.25 s on a worker thread.

The watches and the grown `wait` of section 5 would cross the bridge again
(from a background task, through the collector thread, through `to_thread`),
so they are written down as requirements instead of implemented.

## 2. What the rebuild stands on

| Spike | Settles | Design consequences |
| --- | --- | --- |
| [01 AnyIO and the async kernel API](../spikes/01-anyio-async-kernel/REPORT.md) | The full kernel lifecycle runs under `anyio.run` with no `run_sync`; AnyIO's checkpointing locks rule out the October freeze; waiting on threads starves the server; Windows works except interrupts and leaks two handles per kernel (jupyter/jupyter_client#1147) | §4 |
| [02 Hypermorph live objects](../spikes/02-hypermorph-live-objects/REPORT.md) | Resources and executions can be live objects if the output body stays outside state and each execution is its own object; the host is Hypermorph's `RuntimeServer` with a `wind_down` that ends MCP's streams; `wait` keeps its meaning as observation | §4 |
| [03 Background work in a kernel](../spikes/03-kernel-background-waits/REPORT.md) | Work a cell starts keeps publishing after it ends; a watch can publish under its own parent through the ContextVars alone; a blocking cell holds every task; an interrupt reaches agents a cell started | §4 |

The dependency versions the numbers were taken with (ipykernel 7.3.0,
jupyter_client 8.10.0, anyio 4.15.1) are still the environment's.

## 3. What moves where

One row per module of `src/loommux` as of `4983e47`. *Keep* means the module's
role survives, though its calls may become async; *rewrite* means its
mechanism is replaced; *Hypermorph* means Hypermorph provides what it does by
hand today; *to be designed* means the spikes do not settle it.

| Module | Today | In the rebuild |
| --- | --- | --- |
| `__init__.py` | Public Python API for persistent sessions | Keep; its exports follow the session's new form |
| `cell_header.py` | Parses the `%%loommux` header | Keep unchanged: the grammar is part of the outer form (section 4) |
| `client.py` | `LeaseAwareClient`: policy discovery, header pinning, heartbeat task | Keep; it runs in clients, not on the daemon's loop |
| `config.py` | Finds and reads `.loommux/` | Keep |
| `daemon.py` | `Daemon`, `Kernel`: the HTTP client inside every kernel | Keep the public surface; whether it gains awaitable methods for watches is to be designed |
| `execution/__init__.py` | Package exports | Keep |
| `execution/events.py` | Presentation events before transport projection | Keep |
| `execution/logs.py` | `LineLog`: append-only output with stable line coordinates | Keep as the body outside state (spike 02 §4.1); whole-output search moves off the loop (spike 01 §3.6) |
| `execution/record.py` | `Execution`: lifecycle facts, `threading.Event`, notices | Rewrite as one live object per execution with a bounded summary as state (spike 02 §4.1–4.2); its token count needs the fast path of spike 01 §3.6 |
| `execution/terminal.py` | Terminal text normalization, a per-character state machine | Keep, with a C-level prefilter so chunks without control characters skip it (spike 01 §3.6) |
| `identity.py` | What a kernel is told about itself and its daemon | Keep |
| `kernel/__init__.py` | Package exports | Keep |
| `kernel/launch.py` | Private runtime root, kernel spec, environment | Keep: spike 01's harness launches kernels this way |
| `kernel/runtime.py` | Process lifetime through the blocking `KernelManager`, with the `run_sync` workarounds | Rewrite on `AsyncKernelManager`: channels with `hb=False`, exit watched through a pidfd, shutdown in a shielded scope; the workarounds lose their reason (spike 01 §4.1, §4.3, §4.4). The Windows exit watcher is to be designed |
| `kernel/session.py` | `KernelSession`: collector thread, `RLock`, reply pages, loommux#002's held interrupt | Rewrite as one async iopub reader per kernel that dispatches by parent `msg_id` (spike 03 §4.4); #002's rule becomes an awaited `execute_input` (spike 03 §4.7) |
| `kernel_extension.py` | Header inside the kernel, forwarding, `loommux` object | Keep; the watch registry would live here (spike 03 §4.1), which is section 5's work, not a move |
| `mcp/__init__.py` | Package exports | Keep |
| `mcp/console.html` | The owner's console; since `9db079c` one card per main kernel with its child kernels inside, selection by rule, then recycle | To be designed: whether it stays beside Hypermorph's generated inspector, which spike 02 §3.3 mounts at `/`, the console's path today |
| `mcp/control.py` | HTTP control plane under `/api/` | Keep the routes (section 4); their handlers stop using `to_thread`. Which routes become Hypermorph commands is to be designed |
| `mcp/entrypoints.py` | Chooses stdio or Streamable HTTP and runs it | The HTTP host becomes `RuntimeServer` with `wind_down` ending sse-starlette streams (spike 02 §4.5); stdio is to be designed |
| `mcp/factory.py` | The eight tools; every session call through `to_thread` | Keep the tools; waits become cancellable observation under `move_on_after` (spike 01 §4.2, spike 02 §4.7) |
| `mcp/lease_ping.py` | Lease renewal on MCP ping | Keep |
| `mcp/presentation.py` | Text rendering of results, the `<loommux-notice>` envelope | Keep |
| `mcp/result.py` | MCP content blocks, image delivery | Keep |
| `mcp/server.py` | Server factory and command entry point | Keep |
| `mcp/stream_presence.py` | Reports standalone GET streams to the connection registry | Keep; the host's shutdown must end these streams (spike 02 §3.3) |
| `notice.py` | The notice channel's bounds | Keep |
| `resource/__init__.py` | Package exports | Keep |
| `resource/connections.py` | Which sessions hold a push stream open | Keep the rules; whether it becomes object state is to be designed |
| `resource/liveness.py` | Judges one lease's standing | Keep: a pure judgment |
| `resource/manager.py` | Registry, admission, lifecycle locks, sweep, retirement | Largely Hypermorph: the directory as a Daemon object's state, admission and draining, `close_object` for retirement after a terminal commit (spike 02 §4.2, §4.6). The lease rules, sweep and naming stay loommux's; how they divide is to be designed |
| `resource/model.py` | Resource, lease and binding types | To be designed with the live objects' state |
| `resource/policy.py` | Immutable lease policy generations | Keep the rules; the policy as a live object (spike 02 §1) is to be designed |
| `resource/routing.py` | Reads a request's session and requested kernel | Keep |
| `resource/settings.py` | Validates lease settings from `config.py` | Keep |
| `session.py` | `IPythonSession`: records, numbering, `_await`, notices, startup files | Rewrite async. Whether a synchronous facade remains for stdio and tests is to be designed (spike 01 §4, "Not covered") |
| `workspace_resolver.py` | Resolves the workspace before tools are exposed | Keep |

`scripts/stress.py` and `examples/kernel-environment/tour.py` are end-to-end
checks and move with the tests.

## 4. What the new skeleton must keep working

The outer form does not change: the eight tools, the HTTP control plane, the
`loommux` client inside every kernel, and the cell header. Everything behind it
may.

The tests are the rail for that. Since round 15 they assert behavior, not
wording (CONTRIBUTING, "What tests assert"), and most enter through an MCP
client or the HTTP control plane against a real kernel. They are what Michael
Feathers calls characterization tests (*Working Effectively with Legacy Code*,
2004): tests that pin down what the system does now, so a change of its inside
shows where behavior moved. Fowler's Strangler Fig Application (2004) grows a
new implementation behind an existing interface and retires the old one piece
by piece; whether the rebuild can proceed that way, module by module behind
the session, or must replace the session layer at once, is for the design to
decide. Either way, the outside the tests speak to is the interface that stays.

Each design document below fixes behavior a user or an agent can see. The
verification table of each names the tests that prove it.

| Document | What it fixes | Verification |
| --- | --- | --- |
| [Coding Agent Control Plane](coding-agent-control-plane-design.md) | The eight-tool loop (§2.2), workspace resolution (§5), kernel bootstrap and private IPython state (§6), the terminal text contract (§7) | Black-box scenarios §10.1–10.7 |
| [Multimodal Output](ipython-mcp-multimodal-output-design.md) | Images as MCP image blocks, order of text and images, detail metadata, limits (§3–§6) | Acceptance §9, items 1–10 |
| [Kernel Resource Daemon](kernel-resource-daemon-design.md) | Main and child kernels and sessions (§2), the tool boundary (§3), lifecycle including restart, kernel exit and the interrupt rule of loommux#002 (§4), lease modes (§5), expiry and orphaned executions (§6), the HTTP control plane's routes (§7), the client (§8), settings (§9) | Its tests; §11's event-loop rules are rewritten by the rebuild, `scripts/stress.py` stays their check |
| [Main Kernel Binding](kernel-binding-design.md) | First call opens `private-<session>`, `--attach NAME [--create]`, names, retention only for named main kernels (§5–§8) | B1–B13 |
| [Cell Header](cell-header-design.md) | The grammar (§3), interpretation inside the kernel (§5), kernel identity and the `loommux` object (§6), forwarding and homes (§7–§8) | H1–H9, K1–K2, P1–P2, F1–F3, S1 |
| [Kernel Control Surface](kernel-control-surface-design.md) | The daemon's panel and a kernel's eight operations (§4), names as paths (§5), forwarding written out (§6) | C1–C7 |
| [Kernel Environment](kernel-environment-design.md) | The configuration directory (§4), startup files (§5), notices and the envelope (§6), IPython's execution numbers (§7), `idle_since` (§8) | E1–E9 |
| [Silence](silence-design.md) | `--wait` and `--silence` as two guards with default 10 s (§1, §4), unbuffered children (§5), duration on the first line | S1–S11 |
| [MCP Connections](mcp-connections.md) | How stdio and Streamable HTTP clients connect | `tests/mcp/test_entrypoints.py` and the examples |

The loommux#002 rule deserves its own line because it is not yet an awaited
fact anywhere: **an interrupt is given to the running execution, not to the
process at the moment it is asked for** (daemon design §4). Held until the
cell's `execute_input`; a cell that was interrupted and got no
`execute_reply` is reported `interrupted`. Proved by
`tests/session/test_session.py::test_an_interrupt_right_after_submission_interrupts_that_cell`
and the message-driven tests in `tests/kernel/test_kernel.py`.

Test directories: `tests/session/` (session against a real kernel),
`tests/kernel/` (adapter and extension), `tests/mcp/` (MCP in process and a
real HTTP daemon through `server_harness.py`), `tests/resource/` (leases,
registry, routing), `tests/execution/` (logs, terminal text), and the
top-level tests for the header, client, configuration and examples.

## 5. The waiting model to grow

This is a requirement, not a specification. It was derived in the
conversation of 2026-10-25, is implemented nowhere, and is to grow in the new
skeleton. Spike 03 measured what it stands on.

- **A cell only starts things.** Waiting inside a cell holds the kernel; the
  only way to free it is `interrupt`, which reaches the whole process group
  and ends the agents the cell started (spike 03 §3.4). Shell job control has
  this split (foreground, `&`, `wait`; BSD csh, then POSIX), and so has Node's
  rule not to block the event loop.
- **A cell registers what it is interested in as a watch**: an awaitable under
  a name, run on the kernel's own loop. Erlang's `monitor` has this form:
  register without blocking, receive `'DOWN'` when it ends, wait elsewhere with
  your own timeout.
- **One `wait` waits on an address**: this cell, a named watch, or whichever
  finishes next, as bash's `wait ID` and `wait -n` (4.3; `-p` names the job
  since 5.1) and `asyncio.wait(..., return_when=FIRST_COMPLETED)`. With
  nothing running and no watch pending it says so at once instead of returning
  silently. The eight tools stay eight.
- **The total and silence timers stay guards**, and a watch's own output feeds
  its silence clock as a cell's does.
- **Giving up a wait costs nothing**: it is an observation (spike 02 §3.4
  measured that an abandoned observation leaves the work running and nothing
  registered).
- **What the agent sees on waking is what the watch said.** A watch is code;
  what it prints and returns is the result, so the one who registers it
  decides whether waking shows a full result or a word, as RFC 7240's
  `Prefer: return=representation` and `return=minimal`, and no second tool
  call is needed to look.

Spike 03 §4 turns these into constraints: the registry on the kernel's loop
under one AnyIO task group attached at the root; ContextVars only, never
`set_parent`; "done" as a display under the watch's parent, not a comm; one
reader dispatching by parent `msg_id`; waits that hold no thread; and a
blocking foreground cell holding every watch.

## 6. Open questions

- **Hypermorph#001** (`playground/Hypermorph/docs/issues/001-state-copy-per-operation.rst`):
  every state operation copies the whole public state. Still reproduced on
  `22b17dda`. The answer in the meantime is spike 02 §4.2: hot objects stay
  small, each execution its own object, a resource holds pointers.
- **Hypermorph#002** (`playground/Hypermorph/docs/issues/002-embedding-host-gaps.rst`):
  the inspector works only at the root, protocol plus inspector has no public
  entry, and `RuntimeServer`'s shutdown does not mention foreign SSE streams.
  Open; the rebuild uses spike 02 §3.3's workarounds until it closes.
- **A synchronous facade for `IPythonSession`**: the session tests drive it
  synchronously, and every tool reaches it through `to_thread`. The workspace
  keeps no compatibility layers, so the design decides whether anything
  synchronous remains, stdio included.
- **pytest**: from pytest-asyncio (`asyncio_mode = "auto"` in `pyproject.toml`)
  to AnyIO's plugin.
- **Windows**: an exit watcher without a pidfd; interrupts still replace the
  kernel (spike 01 §3.4); the launcher handle leak until
  jupyter/jupyter_client#1147 is fixed upstream; the nested Job Object of
  loommux#001.
- **Do `%%bash` and `!command` yield to the loop** while their process runs?
  If not, a watch makes no progress during a test run (spike 03 §5).
- **How a watch ends** when cancelled, interrupted or failing, what `wait`
  reports then, and what happens to watches across a restart (spike 03 §5).
- **ipykernel's private ContextVars** (`_parent_header` on the output streams,
  display publisher, display hook and shell) that a watch sets: the design
  must name them and the ipykernel version it relies on (spike 03 §4.2).
- **The console and the inspector** (section 3, `mcp/console.html`).

## 7. Reading plan for the design document

Read in full before the first proposal, as always:
`.aktant/wbs_ipython/docs/一隅三反录.md`.

Before deciding the skeleton (the evidence):
the three spike reports; in Hypermorph, `docs/reference/runtime.rst`,
`docs/reference/api.rst`, `docs/explanation/state-observation.rst`,
`docs/explanation/ui-projection.rst`, `docs/how-to/attach-async-source.rst`,
`src/hypermorph/http/application.py`, `src/hypermorph/http/server.py`,
`src/hypermorph/workbench/service.py`, `src/hypermorph/examples/run_board/` and
`src/hypermorph/examples/queue_desk/application.py`; and
`.aktant/hora/src/hora/live.py`, Hora's buddies, as an existing Hypermorph
application.

Before writing what moves (the code, as of the commit the design starts from):
`session.py`; `kernel/{runtime,session,launch}.py`;
`execution/{record,logs,terminal}.py`;
`resource/{manager,model,policy,liveness,connections,routing,settings}.py`;
`mcp/{factory,control,result,presentation,entrypoints,stream_presence,lease_ping}.py`;
`mcp/console.html`; `kernel_extension.py`; `daemon.py`; `client.py`;
`notice.py`; `cell_header.py`.

Before writing what must keep working: the design documents of section 4, in
the order of the table, and `CONTRIBUTING.md`.

Before writing the waiting model: spike 03 in full, and kernel-environment
design §6 (notices), whose delivery rules a watch's result will share.
