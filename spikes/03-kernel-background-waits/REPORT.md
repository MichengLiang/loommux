# Spike 03: Background work in a kernel, and waiting on it from the tool

> 2026-10-26. Linux/WSL2 (32 CPUs, Python 3.13.16). ipykernel 7.3.0,
> IPython 9.17.1, jupyter_client 8.10.0, pyzmq 27.2.0, comm 0.2.3,
> anyio 4.15.1, fastmcp 3.4.7, mcp 1.30.0, claude-agent-sdk 0.2.168; loommux
> at `9db079c`. First run 2026-10-25 from `tmp/spike-wait`; every number here
> is from the rerun, under `systemd-run --user --scope -p MemoryMax=2G`.

## 1. Why

Today a cell that has to wait for something outside it, a buddy's reply or a
job, waits inside the cell: `await twin.wait(1450)` under
`%%loommux --wait 1500`. Two waits are stacked, and only the outer one can be
let go. Giving up the tool call changes nothing, while the cell holds the
kernel until it ends; the only way to free the kernel is `interrupt`, which
jupyter_client sends to the kernel's whole process group, so the buddy started
from that kernel dies with it. It happened during this spike: an interrupt
meant for one sleeping cell ended a clone's Claude Code session.

The conversation of 2026-10-25 derived a different shape:

- **A cell only starts things; waiting happens at the tool.** The agent's
  event loop (Claude Code) is held by one hanging tool call; the kernel's
  event loop is never held. Shell job control has the same split (foreground,
  `&`, `wait`), and so has Node's rule not to block the event loop.
- **A cell registers what it is interested in**, a *watch*: an awaitable under
  a name, run as a task of the kernel's own loop. Erlang's `monitor` has this
  form: register without blocking, get a message when it ends, wait elsewhere
  with your own timeout.
- **One `wait` tool waits on an address**: this cell, a watch, or whichever
  finishes first (bash `wait` / `wait -n`, `asyncio.wait(..., return_when=
  FIRST_COMPLETED)`), with the total and silence timers as guards.
- **Giving up a wait costs nothing**: it is an observation, and an abandoned
  observation leaves the observed work running and nothing behind.
- **What the agent sees on waking is what the watch said.** A watch is code:
  it prints, displays and returns like a cell. Whoever registers it decides
  then what the wakeup shows, a full result or a word (RFC 7240's
  `Prefer: return=representation` / `return=minimal`), so seeing it needs no
  second tool call.

That shape rests on four facts neither spike 01 nor spike 02 measured:

1. After a cell ends, does work it started still reach iopub, and as whose?
2. Can a watch's output carry the watch's own parent header, so the daemon
   can tell watches apart from cells and from each other?
3. Does an abandoned `wait` tool call let go of what it held on the server?
4. Can a kernel's interrupt spare a background agent the cell started?

## 2. Findings at a glance

| # | Question | Answer |
|---|---|---|
| 1a | Does a task or thread started by a cell run after the cell ends? | Yes: the kernel's asyncio loop turns between cells; the task completes and the next cell reads its result |
| 1b | Does its output reach iopub? | Yes, stream, display and comm alike, named as the **creating cell's** output, even while another cell runs |
| 1c | Does a background task run while a cell blocks? | **No.** A cell in `time.sleep(1.5)` held a task due 0.5 s in; it ran 0.01 s after the cell ended. Threads are not held |
| 2a | Can each watch's output name the watch? | Yes: setting the parent header inside the watch's task separates two watches of one cell, prints and displays alike, flushed or buffered |
| 2b | Does `shell.set_parent` do it cleanly? | **No.** It also moves each publisher's process-wide fallback, so a plain thread of the cell printed as the watch's output |
| 2c | Setting only the ContextVars? | Clean: the thread stays the cell's, the next cell's thread is the next cell's |
| 2d | Comm as the "watch done" channel? | Not without more: a comm message names the cell that created the comm whatever the watch sets; a display does name the watch |
| 3 | Does an abandoned `wait` let go? | **No, at HEAD.** Three abandoned `wait(20)` calls left 3 server threads in `_await` until the cell ended (spike 01 §3.3's starvation, reproduced) |
| 4a | Who does the kernel interrupt? | Its whole process group: `LocalProvisioner.send_signal` calls `os.killpg`; the Agent SDK starts `claude` with a plain `anyio.open_process`, so a buddy is in that group |
| 4b | Does `setsid` spare a child? | Yes: `anyio.open_process(["setsid", ...])` keeps the pid and takes its own group; `killpg(SIGINT)` killed the plain child (returncode −2) and left the other running |

## 3. Experiments

`harness.py` starts a bare ipykernel with jupyter_client, no loommux in
between: the daemon's collector keeps only the current cell's messages, and
the question is what ipykernel itself publishes. It keeps every iopub message
until a settle time after the request's idle and prints each one's parent.

### 3.1 Background output (`probe_background_output.py`)

Cell A creates an asyncio task and a thread, opens a comm, and ends; the task
prints, displays and sends on the comm after 1 s, the thread prints and sends
after 1.5 s. Cell B reads the task's result. Cell C creates a task due 0.5 s
later; cell D, started at once, sleeps 1.5 s without yielding.

```text
--- cell A, and 3 s after its idle  (this request: #1)
  comm_open      parent=cell A       "{'opened': True}"
  stream         parent=cell A       'loop running in the cell: True\ncell A ends\n'
  stream         parent=cell A       'task prints after its cell\n'
  display_data   parent=cell A       "{'application/x-probe': 'task display'}"
  comm_msg       parent=cell A       "{'done': 'task'}"
  stream         parent=cell A       'thread prints after its cell\n'
  comm_msg       parent=cell A       "{'done': 'thread'}"
--- cell B  (this request: #2)
  stream         parent=cell B       'True 42\n'
--- cell D (C's task is due 0.5 s into it)  (this request: #4)
  stream         parent=cell D       'cell D starts at 256321.52\n'
  stream         parent=cell D       'cell D ends at 256323.02\n'
  stream         parent=cell C       "C's task prints at 256323.03\n"
```

A task copies the context it was created in, and ipykernel keeps a request's
parent header in ContextVars, so background output names the cell that
started it, never the cell running at the time. A thread has no copied
context and falls back to the publishers' process-wide value, which was still
cell A's; started under a later cell it would have named that one.

Cell D shows the limit: a task of the kernel's loop runs only when the main
thread yields. A cell that blocks without awaiting holds every task until it
ends; a thread keeps running.

### 3.2 Each watch under its own parent (`probe_watch_parent.py`)

Cell A defines `watched(watch_id, body)`: set a parent header naming
`watch_id`, then await `body`. It starts two watches, `watch-1` (prints at
0.5 s) and `watch-2` (prints, displays and sends on a comm at 0.8 s), and a
plain thread that prints at 1.2 s, after both have set their headers. Cell B
starts another thread and reads both results. Two ways to set the header:

- `shell`: `get_ipython().set_parent(...)`, the public call.
- `context`: only the ContextVars of `sys.stdout`, `sys.stderr`,
  `display_pub`, `displayhook` and the shell, each set to the header itself
  (`extract_header(parent)`, as their `set_parent` stores it; the whole
  message there makes publishing fail with `unhashable type: 'dict'`).

| message | `shell` | `context` | `context`, prints not flushed |
|---|---|---|---|
| watch-1's print | watch-1 | watch-1 | watch-1 |
| watch-2's print | watch-2 | watch-2 | watch-2 |
| watch-2's display | watch-2 | watch-2 | watch-2 |
| watch-2's comm message | cell A | cell A | cell A |
| cell A's thread, printing after both watches set their headers | **watch-2** | cell A | cell A |
| cell B's thread | cell B | cell B | cell B |

`set_parent` on each publisher writes both its ContextVar and its
`_parent_header_global` (`iostream.py`, `zmqshell.py`, `displayhook.py` in
ipykernel 7.3), the value a thread without a parent of its own reads. A watch
setting it moves every such thread's output to the watch until the next cell
starts. The ContextVars alone reach only the task: an asyncio task runs in its
own copy of the context. Unflushed text leaves through ipykernel's buffered
flush under the parent it was written with.

A comm message takes its parent from the kernel itself
(`parent=self.kernel.get_parent()`, `ipykernel/comm/comm.py:42`), whose
`_shell_parent` ContextVar neither way touches; the task's copy of it still
holds the creating cell, so the message names cell A. Setting that variable
too would move comms, but a display already goes where the watch's other
output goes.

### 3.3 An abandoned wait (`probe_abandoned_wait.py`)

An in-process FastMCP client against `create_mcp("content")` at `9db079c`. A
cell runs 30 s of short sleeps; three `wait(20)` calls are each abandoned after
1 s with `move_on_after`; then the server threads whose stack is inside
`IPythonSession._await` are counted.

| moment | threads in `_await` |
|---|---:|
| before | 0 |
| after abandoning 3 waits | 3 |
| a `status` call beside them | answered in 0.002 s |
| after the cell ends (interrupted) | 0 |

`mcp/factory.py` runs each session operation through `asyncio.to_thread`;
cancelling the tool call releases the coroutine, not the thread, which keeps
polling until its own timeout or the cell's end. An abandoned `wait(1450)`
holds a worker for up to 24 minutes. The pool is shared by every tool of every
client; spike 01 §3.3 measured where that ends (the 33rd waiter stalls every
other call for ~55 s), so this probe stops at showing the threads stay.

### 3.4 Background agents and the interrupt (`probe_background_children.py`)

Read from the installed source: `LocalProvisioner.send_signal` signals with
`os.killpg(self.pgid, signum)`, and the Agent SDK starts `claude` with
`anyio.open_process(...)` (`subprocess_cli.py:873`) and no
`start_new_session`, `process_group` or `setsid`, so every buddy and clone a
cell starts is in the kernel's process group.

The probe stands in for a kernel (run under `setsid` so it leads its group),
starts `sleep 30` plainly and through `setsid`, ignores SIGINT itself and
signals its own group:

```text
plain   pid 4049193 pgid 4049191 in the kernel's group: True
setsid  pid 4049194 pgid 4049194 in the kernel's group: False
after killpg(SIGINT): plain returncode -2 · setsid returncode None
```

`setsid` executes in place when its caller does not lead a group, so the pid
the parent holds stays the agent's pid. shellwork's jobs already start with
`start_new_session=True` and are spared.

## 4. What this means for the design

1. **The watch registry lives in the kernel, on the kernel's loop, written
   with AnyIO.** The one place that cannot be AnyIO is the root: the loommux
   extension attaches one task to the asyncio loop ipykernel is already
   running, and that task holds an AnyIO task group; each watch is
   `start_soon` in it and cancelled through its scope. Everything inside a
   watch, and every primitive the registry uses, is AnyIO.
2. **A watch sets only the ContextVars**, never `set_parent`, so threads and
   cells keep their own output (3.2). These are ipykernel's private attributes
   (`_parent_header`); the design must name them and the version it relies on.
3. **"This watch is done" is a display with loommux's own MIME type under the
   watch's parent**, as notices are; not a comm, whose parent is the creating
   cell's (3.2).
4. **One iopub reader per kernel dispatches by parent `msg_id`**: a cell's
   messages to its execution, a watch's to the watch's record (output, silence
   clock, notices, result). Output a background task writes under its creating
   cell (3.1) has an owner the daemon already knows, the finished execution, so
   a reader that keeps it instead of dropping it is a choice the design makes.
5. **A blocking foreground cell holds every watch** (3.1). A watch waiting on a
   buddy makes no progress while a cell sleeps without awaiting. The guidance
   that cells only start things is what keeps watches moving; the `wait` tool
   can report that the kernel is busy beside a watch that has not finished.
6. **Waiting must not hold a thread** (3.3): `wait` becomes cancellable
   observation on the daemon's loop, spike 01's `move_on_after` around an
   event; across several addresses, the first to finish cancels the rest.
   This comes before watches, since a wait that cannot be let go is what the
   whole shape was meant to remove.
7. **loommux#002's rule carries over as an awaited fact**: an interrupt waits
   for the execution's `execute_input`; in the async adapter that is an event
   the reader sets, not a lock and three flags.
8. **Agents a cell starts get their own process group** (3.4). That is Hora's
   change (a `cli_path` wrapper that runs `exec setsid claude "$@"`), not
   loommux's; it stands on its own and waits for no rebuild.

## 5. Not covered

- How a watch ends when cancelled, interrupted or failing, and what `wait`
  reports then; whether an interrupt of a foreground cell reaches the kernel's
  tasks.
- Watches across a kernel restart, and the Windows interrupt that replaces the
  kernel process.
- Whether `%%bash`, `!command` and IPython's other subprocess magics yield to
  the loop while they run; 3.1 measured only `time.sleep`.
- Windows, and uvloop.

## 6. Reproduce

From this directory, with the workbench environment:

```sh
systemd-run --user --scope --quiet -p MemoryMax=2G -p TasksMax=256 python probe_background_output.py
systemd-run --user --scope --quiet -p MemoryMax=2G -p TasksMax=256 python probe_watch_parent.py shell
systemd-run --user --scope --quiet -p MemoryMax=2G -p TasksMax=256 python probe_watch_parent.py context
systemd-run --user --scope --quiet -p MemoryMax=2G -p TasksMax=256 python probe_watch_parent.py context buffered
systemd-run --user --scope --quiet -p MemoryMax=2G -p TasksMax=256 python probe_abandoned_wait.py
systemd-run --user --scope --quiet -p MemoryMax=2G -p TasksMax=256 setsid python probe_background_children.py
```

`probe_abandoned_wait.py` imports loommux from the environment, so it measures
whatever the working tree holds; the numbers above are `9db079c`'s.
