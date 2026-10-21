# Spike 01: AnyIO and jupyter_client's async API

> 2026-10-22. Linux/WSL2 (32 CPUs, Python 3.13.16) and Windows 11 26200
> (Python 3.13.3). jupyter_client 8.10.0, pyzmq 27.2.0, ipykernel 7.3.0,
> anyio 4.15.1, uvicorn 0.52.4, mcp 1.30.0, fastmcp 3.4.7.

## 1. Why

The daemon drives every kernel through jupyter_client's *blocking* API from
worker threads: `KernelManager` and `BlockingKernelClient`, one iopub
collector thread per kernel polling every 100 ms, and every MCP tool call's
session operation run through `asyncio.to_thread` (`mcp/factory.py`). Each
blocking call goes through `jupyter_core.run_sync`, which caches event loops
in a ContextVar. The freeze and the leaks of rounds 3 and 4 came from this
bridge: an event loop that never yielded, `run_sync` waiting without a
timeout, a loop leaked per call (upstream jupyter/jupyter_core#466), and the
private `contextvars.Context` and `close_jupyter_event_loop` written to
contain them.

jupyter_server never meets these problems because it uses jupyter_client's
native `AsyncKernelManager` and `AsyncKernelClient`. The migration considered
here moves the daemon onto that API and writes its own concurrency with AnyIO
rather than raw asyncio. This spike settles what that depends on before a
design document commits to it.

## 2. Findings at a glance

| # | Question | Answer |
|---|---|---|
| 0 | Does AnyIO remove the "await that never yields" behind the freeze? | Yes: an uncontended `anyio.Lock` yields, `asyncio.Lock` does not |
| 1 | Does the full kernel lifecycle work under `anyio.run`? | Yes; zero entries into `run_sync`; process back to baseline |
| 2 | Do churn and cancellation stay clean? | Yes, provided kernel owners shut down in a shielded scope |
| 3 | Does a waiting tool call starve the current server? | **Yes**: the 33rd concurrent waiter blocks every other tool call for ~55 s |
| 4 | Windows? | Works on the default Proactor loop; interrupt still does not; **jupyter_client leaks 2 handles per kernel launch**, before and after the migration |
| 5 | uvloop (uvicorn's choice when installed)? | Works identically |
| 6 | Which record operations would block the loop? | Terminal normalization of large chunks and the first token count; both have cheap fast paths |
| 7 | Does the whole shape hold on a real uvicorn server? | Yes: one thread under load, kernel death noticed in 2.5 ms, clean shutdown |

## 3. Experiments

All probes launch kernels the way the daemon does (private runtime root, IPC
transport, a server-authored `KernelSpec`), through `harness.py`. `harness.py`
counts every entry into `jupyter_core.run_sync`'s two paths by patching the
module globals it looks up at call time. Process facts come from `/proc`
(file descriptors by kind, Python threads by name, live children).

### 3.0 Lock checkpoint (run in an IPython cell)

A loop of uncontended `async with lock:` beside one task waiting to run, the
shape of `KernelResourceManager.operation()` when it froze:

- `asyncio.Lock`: the bystander had not run after 10 000 iterations.
- `anyio.Lock`: the bystander ran after the first. Its `acquire` calls
  `cancel_shielded_checkpoint()` on the uncontended path unless constructed
  with `fast_acquire=True`.

AnyIO inherits Trio's rule that every async primitive is a checkpoint (a
scheduling point and a cancellation point). Under it the October freeze could
not have happened.

### 3.1 Lifecycle (`probe_lifecycle.py`)

Start, execute (`hi`, `42`), `len?` (the page arrives in the execute_reply
payload, as today), interrupt (→ `KeyboardInterrupt`), restart (namespace
empty), shutdown, all inside `anyio.run(backend="asyncio")`.

| | before | running | after |
|---|---:|---:|---:|
| fds | 7 | 32 | 7 |
| eventpoll | 1 | 6 | 1 |
| threads | Main | Main, Thread-1 | Main |

- `run_sync` entries: 0. Children left: none.
- Steps: start 0.51 s, interrupt round trip 1.02 s, restart 0.60 s, shutdown 0.11 s.
- `Thread-1` is the heartbeat channel: `HBChannel` is a `threading.Thread`.
  The daemon checks liveness on the process, so `start_channels(hb=False)`
  leaves no thread per kernel.
- Trap: on `AsyncKernelManager`, `cleanup_resources` is a coroutine and a bare
  call silently does nothing; `shutdown_kernel` already awaits it.

### 3.2 Churn and cancellation (`probe_churn_cancel.py`)

Churn: 120 kernels, 8 at a time, start/execute/shutdown with `hb=False`, in
11.0–11.2 s. fds 7 → 7, threads Main → Main, children none.

Cancelled start, `move_on_after(delay)` around `start_kernel` plus a sleep:

| delay (s) | cleanup | orphaned kernels | leaked fds |
|---:|---|---:|---:|
| 0.0, 0.02, 0.1, 0.3 | none | 1 each | 7 each |
| 0.0, 0.02, 0.1, 0.3 | `finally:` + `CancelScope(shield=True)` + `shutdown_kernel` | 0 | 0 |

Even a cancellation at 0.0 s finds the kernel process launched:
`start_kernel` spawns before its first cancellation point. `manager.has_kernel`
is already true then, so the shielded cleanup finds it.

Cancelled receive: a cell prints 2000 lines while the reader wraps every
`get_iopub_msg()` in `move_on_after(0.0005)`. 134 and 167 cancellations in two
runs; 2000/2000 lines received in order; the channel keeps working.
Cancelling a receive does not lose the message.

### 3.3 Thread-pool starvation (`probe_thread_pool.py`)

Side A is a real server exported from commit 7a863e6 (`git archive`, run with
`PYTHONPATH` ahead of the editable install). Every tool runs its session
operation through `asyncio.to_thread`, whose default executor here has
min(32, CPUs + 4) = 32 workers. N clients call `wait(60)` on a shared kernel
that sleeps; five seconds later another client calls `status` on its own,
already provisioned, private kernel, and the console is read.

| N waiters | console `GET /api/resources` | bystander `status` |
|---:|---:|---:|
| 8 | 0.00 s | 0.01 s |
| 31 | 0.00 s | 0.00 s |
| 32 | 0.00 s | **55.18 s** |
| 40 | 0.01 s | **55.40 s** |

A waiting call holds a worker for the whole wait. Once 32 calls wait, every
tool call of every client queues until a wait ends, while the console, which
does not use the pool, still answers: the server looks healthy and does
nothing. `wait`, `run_cell --wait`, and forwarded cells all wait this way, and
the pool size depends on the CPU count. `MAX_FOLLOW_WAIT_SECONDS` in
`session.py` already caps follow requests at one second for this reason.

Side B models the post-migration wait, an `anyio.Event` under
`move_on_after`: 1000 concurrent waiters, one thread, a bystander's sleeps
late by at most 0.7 ms.

### 3.4 Windows (`probe_windows.py`, `probe_windows_sync.py`)

Run from an ephemeral environment (`uv run --no-project --with jupyter_client
--with ipykernel --with anyio --with psutil`) in a Windows temporary
directory; handles, threads, and children come from psutil.

- The default `ProactorEventLoop` works. pyzmq warns once that Proactor lacks
  `add_reader` and starts one process-wide "Tornado selector" thread.
- Execute works; `run_sync` entries: 0.
- Interrupt does not arrive: no `KeyboardInterrupt` within 10 s. The daemon's
  existing Windows path (replace the kernel) stays. Not investigated: each
  kernel runs as two processes, a `python.exe` launcher (venv and uv
  interpreters on Windows are redirectors) and the real interpreter, which
  may be why the inherited interrupt event is not seen; see jupyter_client#387.
- Handles grow and a full collection does not release them:

  | probe | per round | per kernel launch |
  |---|---:|---:|
  | async, 20 kernels churned | +40 | ~2 |
  | async, 5 cancelled starts | +10 | ~2 |
  | sync (today's API), 20 kernels | 154 → 219 → 261 → 300 | ~2 |
  | sync, contexts destroyed after shutdown | 154 → 219 → 261 → 300 | ~2 |
  | sync, launcher handles closed after shutdown | 154 → 179 → 181 → 180 | 0 |

  Cause: on Windows, `jupyter_client.launcher.launch_kernel` creates an
  interrupt event (`create_interrupt_event()`, launcher.py line 99) and an
  inheritable duplicate of the parent process handle (`DuplicateHandle`, line
  124) for every kernel, and nothing in jupyter_client closes either. Closing
  the two recorded handles after shutdown stops the growth. The leak predates
  the migration and is not caused by it. No upstream issue reports it (searched
  jupyter/jupyter_client issues and pull requests on 2026-10-22).

### 3.5 uvloop (`SPIKE_UVLOOP=1 uv run --with uvloop python probe_lifecycle.py`)

uvicorn's `loop="auto"` picks uvloop whenever it is installed, as with
`uvicorn[standard]`, and zmq.asyncio needs the loop's `add_reader` family.
Under `backend_options={"use_uvloop": True}` every step of 3.1 behaves the
same, with the same timings; `run_sync` entries 0; fds back to baseline.

### 3.6 Cost on the loop (`probe_loop_cost.py`)

After the migration, appends run where iopub messages are awaited and reads
where tool calls are awaited, both on the event loop; one call's duration is
how long every other request waits. Measured on the working tree's
`Execution`, with 72-character log lines:

| total | iopub chunk | append, worst chunk | first `snapshot()` | search literal / regex | read full |
|---:|---:|---:|---:|---:|---:|
| 1 MB | 4 KB | 0.9 ms | 194 ms | 4.5 / 6.1 ms | 0.7 ms |
| 1 MB | 64 KB | 10.7 ms | 53 ms | 5.1 / 6.7 ms | 0.7 ms |
| 10 MB | 64 KB | 10.1 ms | 561 ms | 67 / 82 ms | 11.5 ms |
| 10 MB | 1 MB | 153.7 ms | 581 ms | 74 / 87 ms | 9.6 ms |
| 40 MB | 1 MB | 159.1 ms | 2160 ms | 273 / 336 ms | 48.3 ms |

Second snapshots, `status_snapshot`, and tail reads cost ~0 ms.

- Appends cost ~150 ms per MB in `TerminalTextNormalizer`, a per-character
  Python state machine. ipykernel batches fast output into large messages, so
  a 1 MB message holds the loop ~155 ms. A C-level prefilter for control
  characters (`[\x00-\x08\x0b-\x1f\x7f-\x9f]`) scans 1 MB in 2.1 ms; chunks
  without a match need no state machine.
- The first snapshot after a cell ends counts the whole output's tokens to
  decide delivery against the 5000-token limit. The decision only needs to
  know whether the limit is exceeded: counting 64 KB pieces until the sum
  passes 5000 took 3.4 ms on 40 MB. Pieces must be cut at line boundaries,
  and an output that only exceeds the limit across a cut could differ by a
  few tokens from a whole count; the design has to state which it accepts.
- Search over large outputs takes tens to hundreds of milliseconds and does
  not need the loop; `anyio.to_thread.run_sync` fits it.

### 3.7 The whole shape on uvicorn (`probe_server_shape.py`)

A Starlette app whose lifespan task group owns a kernel, the task reading its
iopub, and a task awaiting the kernel's exit through a pidfd
(`os.pidfd_open` + `anyio.wait_readable`); requests execute and wait under
`move_on_after`. Served by `uvicorn.Server` inside `anyio.run`.

- 40 concurrent requests each waiting 2 s on a busy kernel; a request beside
  them answered in 4.7 ms; all 40 returned "still running" after 2.03 s.
  Threads during the load: MainThread only.
- The kernel killed with SIGKILL from outside: its exit noticed in 2.5 ms,
  without polling.
- Server shutdown cancels the group and shuts the kernel down in a shielded
  scope: fds 7 → 7, threads Main → Main, children none, `run_sync` entries 0.

## 4. What this means for the migration design

1. **Adopt `AsyncKernelManager` / `AsyncKernelClient`, written with AnyIO on
   the asyncio backend.** Trio is not available: zmq.asyncio and uvicorn need
   asyncio. The MCP SDK and Starlette already use AnyIO. The private Jupyter
   context, `close_jupyter_event_loop`, the process-poll liveness workaround,
   the collector threads, and their locks lose their reason to exist.
2. **Waiting moves off threads.** `wait`, `run_cell`'s initial wait, and
   forward following become `move_on_after` around an event. This removes the
   starvation of 3.3 and the one-second follow cap that worked around it.
3. **Structured ownership.** Each resource owns a task group (iopub reader,
   exit watcher); retiring a resource cancels it. Anything that may own a
   kernel shuts it down in `finally` under `CancelScope(shield=True)`, because
   a cancelled start has already launched the process (3.2).
4. **Start channels with `hb=False`.** Liveness comes from the process: a pidfd
   on Linux; Windows has no pidfd and needs another wait (polling the process,
   or a wait on its handle), to be chosen in the design.
5. **Budget for the loop.** Fast paths for normalization and the token limit
   (3.6) before the move; search and other whole-output scans off the loop.
   Design §11's discipline is rewritten around "the loop is the only thread".
6. **Windows.** Keep kernel replacement for interrupts. Close the two launcher
   handles after shutdown, independent of the migration, and report the leak
   upstream.
7. **Use AnyIO's own primitives deliberately.** `anyio.Lock` checkpoints by
   default; `fast_acquire=True` gives that up and is not wanted here.

Not covered here, for the design to decide: whether `IPythonSession` keeps a
synchronous facade for stdio and tests (the workspace's rule is no
compatibility layers), the pytest move from pytest-asyncio to AnyIO's plugin,
and how a Windows exit watcher waits.

## 5. Reproducing

From this directory, with the workbench environment:

```sh
python probe_lifecycle.py
python probe_churn_cancel.py 120
python probe_loop_cost.py
python probe_server_shape.py
SPIKE_UVLOOP=1 uv run --with uvloop python probe_lifecycle.py
python probe_thread_pool.py b
```

`probe_thread_pool.py a URL N` needs a server of the pre-migration design on
URL; the numbers above used `git archive 7a863e6 src` with `PYTHONPATH`
pointing at the export. The Windows probes run from a Windows directory with
`uv run --no-project --with jupyter_client --with ipykernel --with anyio --with psutil python probe_windows.py`
(and `probe_windows_sync.py default|destroy|close`, without anyio). The probes
here were run under `systemd-run --user --scope -p MemoryMax=…` caps.
