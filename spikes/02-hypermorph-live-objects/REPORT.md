# Spike 02: Hypermorph live objects as loommux's runtime

> 2026-10-22. Linux/WSL2, Python 3.13.16. Hypermorph at workbench `d3504a4b`
> (uv workspace member), anyio 4.15.1, jupyter_client 8.10.0, ipykernel 7.3.0,
> fastapi 0.141.1, starlette 1.7.0, uvicorn 0.52.4, hypercorn 0.18.0,
> sse-starlette 3.4.11, mcp 1.30.0, fastmcp 3.4.7. Every probe ran in its own
> process under `systemd-run --user --scope -p MemoryMax=…` with a deadline.

## 1. Why

Spike 01 settled that the daemon can drive kernels with jupyter_client's async
API under AnyIO. The rebuild agreed after it goes one step further: loommux's
domain facts — the daemon's directory, each kernel resource, each execution,
the lease policy — become Hypermorph live objects. Hypermorph then supplies what
loommux writes by hand today: identity, ordered transactions, admission and
draining, pull (`snapshot`) and push (`watch`) observation, guards, object
ending, a generated inspector and remote handles. loommux keeps the kernel
adapter, the lease rules, LineLog and the MCP shape mapping.

Four things had to be measured before a design commits to that, plus one that
surfaced while reading Hypermorph's host:

1. What does one transaction per iopub chunk cost? Hypermorph resolves the full
   public state before every commit, so where must the output body live?
2. Can Hypermorph's HTTP app and inspector be mounted inside loommux's own
   Starlette application, with the Runtime owned by loommux's lifespan?
3. Hypermorph does not push directory changes. Is the resource directory as the
   state of a Daemon object enough?
4. Do MCP `wait` and `run_cell --wait` keep their meaning when they become
   "observe the Execution until it is terminal or the deadline passes"?
5. (Emerged) Does the host shut down while observation streams — Hypermorph's
   and MCP's GET stream — are still open?

## 2. Findings at a glance

| # | Question | Answer |
|---|---|---|
| 1a | Body as state? | **No.** Per-commit cost grows with the body: 15 ms at 1 MB, 0.6 s at 40 MB; one commit held the loop for 103 s |
| 1b | Summary as state, body outside? | ~0.2–0.3 ms per commit, flat from 1 to 40 MB; commands wait ≤1.5 ms behind the stream |
| 1c | Does the rest of an object's state tax a small commit? | **Yes, linearly**: a one-bit change costs 0.8 ms beside 100 summaries, 7.3 ms beside 1000. Cause: `StateAccess` copies the whole public state several times per operation |
| 1d | One live object per execution? | Cheap: ~20 µs to open, ~5 µs to close, ~3 KiB each |
| 1e | Real kernel through the Runtime | ipykernel batches into MiB-sized messages (10 MB arrived as one); the Runtime adds little; a 40 MB message holds the loop ~120 ms; interrupt under a flood reaches a terminal Execution in ~50 ms |
| 2 | Mount inside our Starlette? | Yes, at `/` behind loommux's routes; the inspector only works at the root |
| 3 | Directory as Daemon state? | Yes; opening inside a command works; recycle orders cleanly as final snapshot → `closed` → directory removal |
| 4 | `wait` as observation? | Same semantics; use `aclosing`; recycle must commit a terminal status before closing |
| 5 | Shutdown with streams open? | uvicorn with the Runtime closed in the lifespan **hangs** (even `force_exit`). Hypermorph's `RuntimeServer` plus a `wind_down` that ends MCP's sse-starlette streams exits in 0.13 s, Ctrl+C included |

## 3. Experiments

`domain.py` holds small objects shaped like loommux's: `TextExecution` (body in
state), `CountedExecution` (status, line and character counts as state; body in
a private list standing in for LineLog), `Resource` (a summary table in state)
and `Daemon` (the directory as state).

### 3.1 Commit cost (`probe_commit_cost.py`)

A source appends `total_mb` in `chunk_kb` chunks; a lag meter sleeps 1 ms in a
loop; a "poke" task invokes an exclusive no-op command every 50 ms (standing in
for `interrupt`); optionally one observer follows.

Summary as state (`CountedExecution`), selected rows:

| total | chunk | observer | µs/commit | MB/s | loop lag max | poke wait max | newcomer baseline |
|---:|---:|:-:|---:|---:|---:|---:|---:|
| 1 MB | 4 KB | – | 215 | 18 | 0.8 ms | 1.0 ms | 0.04 ms |
| 40 MB | 4 KB | – | 187 | 21 | 1.0 ms | 1.0 ms | 0.03 ms |
| 40 MB | 4 KB | 1 | 241 | 16 | 0.9 ms | 0.5 ms | 0.08 ms |
| 40 MB | 64 KB | – | 228 | 275 | 0.9 ms | 1.1 ms | 0.03 ms |
| 40 MB | 1 MB | – | 555 | 1801 | 1.0 ms | 1.5 ms | 0.03 ms |

The cost is fixed per commit and does not grow with the body. At 4 KB chunks
the fixed cost caps throughput near 20 MB/s; §3.2 shows real chunks are far
larger. An observer adds 15–25 %.

Body as state (`TextExecution`), 60 s budget per case:

| total | chunk | sent / planned | ms/commit | loop lag max |
|---:|---:|---:|---:|---:|
| 1 MB | 4 KB | 256 / 256 | 15.1 | 33 ms |
| 1 MB | 1 MB | 1 / 1 | 34.9 | 24 ms |
| 10 MB | 4 KB | 1127 / 2560 | 53.3 | 172 ms |
| 10 MB | 64 KB | 35 / 160 | 3075 | **103 s** |
| 10 MB | 1 MB | 10 / 10 | 134 | 272 ms |
| 40 MB | 1 MB | 40 / 40 | 594 | 1.3 s |
| 40 MB | 64 KB, observer | — | no result in 150 s | — |

Each commit resolves, diffs and copies the whole body, so the total is
quadratic and a single commit can stall the loop for as long as it likes.

Neighbour state (`Resource`): 2000 commits that flip one boolean while the same
object holds N execution summaries.

| N | µs/commit | writing the N summaries in one transaction |
|---:|---:|---:|
| 0 | 136 | – |
| 100 | 817 | 46 ms |
| 1000 | 7 318 | 3.27 s |
| 5000 | no result in 150 s | – |

The second column grows with N squared. A profile of writing 400 summaries
(3.7 s) puts 3.6 s in `copy.deepcopy`: `StateAccess._apply`
(`hypermorph/objects/live.py:87`) takes a full `snapshot()` (a deep copy of every
public field), builds a `Snapshot`, applies the one operation through
`apply_update`, converts back with `to_dict` and restores with another deep copy
— the whole public state is copied about five times per `set`/`append`, however
small the operation.

One object per execution: opening 1000 / 5000 `CountedExecution` objects costs
22 / 17 µs each, closing 5.9 / 5.5 µs each, about 3 KiB each in resident
memory; listing 5000 objects takes 0.7 ms.

### 3.2 A real kernel as the source (`probe_kernel_source.py`)

Spike 01's harness launches the kernel (private root, IPC, no heartbeat
channel). `raw` reads iopub directly; `live` attaches the same iopub reader to a
`CountedExecution` with an observer, the poke task and the lag meter running.

| shape | total | mode | s | MB/s | chunks | median chunk | loop lag max | poke wait max |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| 100-byte lines | 10 MB | raw | 0.244 | 41 | 1 | 10 MiB | – | – |
| 100-byte lines | 10 MB | live | 0.312 | 32 | 1 | 10 MiB | 28 ms | 0.6 ms |
| 100-byte lines | 40 MB | raw | 0.790 | 51 | 4 | 11.9 MiB | – | – |
| 100-byte lines | 40 MB | live | 0.823 | 49 | 4 | 11.3 MiB | 38 ms | 0.6 ms |
| 1 MB writes | 40 MB | raw | 0.210 | 190 | 1 | 40 MiB | – | – |
| 1 MB writes | 40 MB | live | 0.311 | 129 | 1 | 40 MiB | 119 ms | 0.5 ms |

ipykernel's own buffering turns any burst of writes into a few MiB-sized
messages, so the number of commits is tiny and §3.1's fixed cost does not
matter. What holds the loop is one huge message: jupyter_client decoding 40 MiB
of JSON plus the per-chunk work on it. The current daemon pays this on a worker
thread; after the migration it lands on the loop, together with the terminal
normalization and token counting spike 01 measured.

Interrupt under a flood (`while True: sys.stdout.write(line)`, ~60 MB/s, three
runs): the exclusive command returned in 0.35–0.39 ms, and from the command to
an `interrupted` Execution took 50–56 ms. No child process was left behind in
any run.

### 3.3 The host (`probe_host_shape.py`)

A Starlette application with loommux's routes first (`/api/ping`; with `mcp`,
a real FastMCP app at `/m`) and `create_inspected_app(runtime)`
(`hypermorph/workbench/service.py:33`) mounted at `/` last. The Runtime belongs
to the application's lifespan. A browser-like client watches the Daemon over
`/objects/watch`; with `mcp`, a real MCP client keeps its session and GET
stream open.

**Mounting.** The inspector page (200), both assets (200), `/objects` and the
loommux routes all answer; the earlier routes win. The inspector's build fixes
its assets at `/assets/` and calls the protocol with absolute paths
(`fetch('/commands')`, `${baseUrl}/objects/watch` with an empty `baseUrl`), so it
works only when mounted at the root. loommux's paths (`/mcp`, `/api/…`) do not
collide with Hypermorph's (`/objects…`, `/object/…`, `/commands`, `/assets`).

**Directory.** `Daemon.open(name)` calls `runtime.open` inside its own command
transaction; that is accepted (HTTP 200). The watching client sees, in order:
the Daemon's `set /resources/k1`; after declaring `k1`, its baseline. Recycling
is `close_object("k1")` (not allowed inside a transaction) followed by a Daemon
submit removing the entry; the client sees k1's final snapshot, `closed`, then
the Daemon's `remove /resources/k1`. Opening an object is a side effect a later
rollback of the same transaction does not undo.

**Shutdown with the streams open** (time from the stop request to `serve`
returning):

| host | Hypermorph SSE open | plus MCP GET stream |
|---|---|---|
| uvicorn, Runtime closed in the lifespan's exit | **hangs** (> 8 s; `force_exit` does not end it within 5 s more) | **hangs** |
| uvicorn, `Server.shutdown` overridden to close the Runtime first | 0.16 s | 0.16 s |
| `RuntimeServer(runtime_owner="application")` | 0.002 s | **hangs** |
| `RuntimeServer` + `wind_down` setting sse-starlette's `AppStatus.should_exit` | 0.003 s | 0.13 s (3/3) |
| same, stopped by SIGINT with `serve(signals=True)` | – | 0.13 s (2/2) |

uvicorn waits for open connections before running the lifespan's exit, and the
observation streams end only when the Runtime closes in that exit: each waits
for the other. `RuntimeServer` is written for exactly this (its module
docstring describes the deadlock): its shutdown trigger runs `wind_down`, then
closes the Runtime so observations end with their final state, and only then
lets Hypercorn drain. MCP's GET stream is an sse-starlette `EventSourceResponse`
(`mcp/server/streamable_http.py:25`); sse-starlette ends its streams on its own
only by hooking uvicorn's `Server.handle_exit`, and its documented server-neutral
switch is `AppStatus.should_exit = True` (`sse_starlette/sse.py:181`). Under
Hypercorn nothing sets it, so the MCP stream holds the drain; Hypercorn's
`graceful_timeout` (3 s by default) did not end it within 8 s. Setting the flag
in `wind_down` — the moment `RuntimeServer` reserves for "stopping is decided,
the Runtime and observations are still open" — closes every MCP stream.

### 3.4 `wait` as observation (`probe_wait.py`)

`wait` observes the Execution inside `aclosing(...)` under `move_on_after`, and
answers `terminal`, `timeout` (with the current running state) or `closed`.

| case | result |
|---|---|
| deadline before completion | `timeout` after 0.301 s with 5 of 20 lines; the execution continues and a second wait returns `terminal` with 20 |
| already finished | `terminal` in 0.06 ms |
| waiter cancelled mid-wait (client gone) | execution completes `ok`; 0 observation registrations left |
| recycled while waited on | `closed`, last status still `running`; `close_object` 0.17 ms; the source ends `cancelled` |
| 1000 waiters on one execution | all woken in the same scheduling round as the final commit; loop lag max 33 ms; 0 registrations left |
| 2000 early exits | `async for … return`: 2 registrations pending finalization (asyncio closes the abandoned generators when their reference count drops); `aclosing`: 0 |

## 4. What this means for the design

1. **The body is not state.** LineLog keeps the output; an Execution's state is
   a bounded summary — status, counts, error summary, times. Followers read new
   text through a read-only (`@modifies()`) command keyed by the character count
   they last saw.
2. **Hot objects stay small.** Every operation and commit costs the size of its
   object's public state (1c). Each execution is its own live object; a Resource
   holds pointers (current and recent execution numbers), never the history. The
   Daemon's directory is bounded by the number of live resources.
3. **Commit overhead is not the bottleneck; huge messages are.** Real chunks are
   MiB-sized, so the Runtime adds little; the loop cost of decoding and handling
   a multi-MiB message stays and must be bounded with spike 01's fast paths.
4. **Commands never queue behind output.** Exclusive commands waited ≤1.5 ms
   behind a 4 KB stream and 0.4 ms behind a real kernel's flood; an interrupt
   reached its terminal Execution in ~50 ms.
5. **The host is `RuntimeServer`.** `runtime_owner="application"`; loommux's
   routes first and `create_inspected_app` at `/`; a `wind_down` that ends MCP's
   sse-starlette streams and commits running executions as interrupted;
   `signals=True` gives the two-step Ctrl+C. `AppStatus.should_exit` is
   process-global, so a test serving twice in one process resets it.
6. **Recycling commits an ending before closing.** A waiter on a closed object
   sees `closed` with a non-terminal status. Recycle first submits the
   execution's terminal status, then `close_object`, then removes the directory
   entry. Opening objects inside a command is allowed but is not rolled back,
   so it happens after everything that can fail.
7. **`wait` keeps its meaning** as observation under a deadline, always through
   `aclosing`.

## 5. Gaps to send back to Hypermorph

Following its CONTRIBUTING loop ("the example story bends where the SDK has a
gap"):

- **State operations copy the whole state** (`objects/live.py:87`). Each
  `set`/`append`/`remove` is O(public state), so N operations in one
  transaction are O(N²) and an object with a large mapping commits slowly. A
  rollback copy is needed once per transaction, not per operation; operations
  could apply in place on the touched path.
- **The inspector only works at the root** (assets fixed at `/assets/`, absolute
  protocol paths). Embedding hosts with their own `/` must give it up.
- **"Protocol plus inspector" has no public entry**: `create_inspected_app`
  lives in `hypermorph.workbench.service`.
- **`wind_down` and foreign streams**: `RuntimeServer`'s documentation explains
  the Runtime/drain deadlock for its own observations; a host that also serves
  sse-starlette streams (any MCP server) meets the same deadlock one level
  over. Worth a sentence and an example.

## 6. Reproduce

From this directory, each case in its own capped process, e.g.:

```bash
systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 \
  python probe_commit_cost.py '{"case":"ingest","variant":"counted","total_mb":40,"chunk_kb":4,"observers":1,"budget":60}'
python probe_commit_cost.py '{"case":"neighbour","entries":1000,"commits":2000}'
python probe_commit_cost.py '{"case":"objects","count":5000}'
python probe_kernel_source.py '{"mode":"live","shape":"lines","total_mb":40}'
python probe_kernel_source.py '{"mode":"interrupt"}'
python probe_host_shape.py '{"host":"runtime-server-signal","mcp":true,"drain":true}'
python probe_wait.py
```

`host` is one of `uvicorn-naive`, `uvicorn-ordered`, `runtime-server`,
`runtime-server-signal`. The body-as-state cases need a deadline: they do not
finish.
