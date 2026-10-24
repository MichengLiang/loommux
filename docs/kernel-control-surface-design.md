# Kernel Control Surface Design

> **Status: Written on October 24, 2026.** This document owns what code
> outside the eight MCP tools can do to kernels: the buttons of the daemon,
> the buttons of one kernel, and how a name finds a kernel. The
> [Cell Header Design](cell-header-design.md) keeps the header grammar and
> forwarding; the [Main Kernel Binding Design](kernel-binding-design.md) keeps
> main kernels, child kernels and retention; the
> [Kernel Resource Daemon Design](kernel-resource-daemon-design.md) keeps
> leases and the HTTP control plane.

## 1. Decision

There are two panels of buttons.

- **The daemon's.** There is one daemon. It holds the registry of kernel
  resources, so it lists them, finds one by name, opens a child kernel, renames
  a kernel, and retires one, the idle ones, or all of them. Birth, death and
  names are the daemon's.
- **A kernel's.** Every kernel answers the same eight operations, with the
  names and parameters of the eight MCP tools: `run_cell`, `status`,
  `execution_status`, `read_output`, `search_output`, `wait`, `interrupt`,
  `restart`. The MCP tools are this panel aimed at the session's main kernel;
  the Python client aims it at any kernel.

A name is a path. A child kernel's name is unique only among the kernels of
its main kernel, so `/main/child` names one kernel anywhere, `/main` names a
main kernel, and a bare `child` is read from the caller's own main kernel. No
kernel name contains `/`.

```python
loommux.kernels()                    # every kernel
loommux["build"]                     # find; never opens
loommux["/小汤试新工具/build"]         # another main kernel's child
build = loommux.create("build")      # open a child kernel of this main kernel, or find it
loommux.rename("build", "pytest台")
loommux.recycle("pytest台", force=False)

build.run_cell("import pytest", wait_seconds=10)   # the kernel's eight
build.read_output(1, line_range="-20:")
build.interrupt()
```

## 2. Where The Shape Comes From

| Rule | Prior art | Here |
| --- | --- | --- |
| One client owns the registry; resources are created, listed and deleted through it by name | Azure SDK: `client.resource_groups.create_or_update`, `.get`, `.list`, `.begin_delete`; `docker rename`, `docker rm` are daemon commands | `loommux.create`, `[]`, `kernels`, `rename`, `recycle` |
| A name is unique inside a namespace; the full name is `namespace/name`; an unqualified name is read from the current context | Kubernetes namespaces and the current context of a kubeconfig | `/main/child`; a bare name from the caller's main kernel |
| A path from the root starts with `/`; a path without one is relative | every file system | `/main`, `/main/child`, `child` |
| Looking up and creating are two acts | a `dict` raises `KeyError` on a missing key; Azure `get` raises `ResourceNotFound`; `--attach NAME` and `--attach NAME --create` | `[]` finds, `create` opens |
| A name is for a person now; an id is for a reference that lasts | Kubernetes `metadata.name` and `metadata.uid` | ids resolve everywhere and never change |
| A cell magic is a function call | IPython rewrites `%%bash` into `get_ipython().run_cell_magic('bash', '', body)` | `%%loommux --kernel build` is a call of the client (section 6) |

## 3. Journeys

1. An agent wants its tests to run beside its namespace.
   `%%loommux --kernel build` opens `build` on first use and relays its output
   into the cell; in Python the same is `loommux.create("build")` followed by
   `run_cell`, `follow` and, if this cell is interrupted, `interrupt`.
2. The agent wants `build` to run while its own main kernel keeps working. It
   writes `build.run_cell(code, wait_seconds=0)` and later
   `build.wait(n, timeout_seconds=600, silence_seconds=60)` and `build.read_output(n)`, the same
   calls its MCP tools make on its main kernel.
3. The vitals alarm in a main kernel lists every kernel and keeps the rows
   whose `home` is its own name: its child kernels.
4. Xiaoliang, in a script outside any kernel, writes
   `Daemon()["/小汤试新工具/build"].interrupt()`. Outside a kernel there is no
   context, so names are read from the root.

## 4. The Two Panels

### 4.1 The daemon's

| Python, `loommux` | HTTP | Console |
| --- | --- | --- |
| `kernels(timeout=60)` | `GET /api/resources` | the list |
| `daemon[path]` | `GET /api/resources/lookup?path=&context=` | |
| `create(path)` | `POST /api/resources` | |
| `rename(kernel, name)` | `POST /api/resources/{id}/rename` | |
| `recycle(kernel, force=False)` | `POST /api/resources/{id}/recycle` | each row's recycle and force |
| `recycle_idle()`, `recycle_all()` | `POST /api/resources/recycle-idle`, `recycle-all` | the buttons at the top |
| `self` | | |

`kernel` is a path, an id, or a handle. The console places recycle on each row
as a file manager places delete beside each file: the button is in the row, the
act is the registry's.

`kernels()` lists every kernel, as the console does. Names do not collide in
the listing, because a child kernel's row carries its `home`, the name of its
main kernel; scoping names never needed scoping the view. `timeout` is how long
to wait for the daemon before `DaemonError`, for code that asks on every cell.

`self` is the kernel this process is, or was started from. `notify` is on the
client too, though it is no button of either panel: it speaks from the current
cell and never reaches the daemon.

### 4.2 A kernel's

| MCP tool | Python, on a handle | HTTP, under `/api/resources/{id}` |
| --- | --- | --- |
| `run_cell(freeform)` | `run_cell(freeform, wait_seconds=None, silence_seconds=None)`, the header's when left out | `POST /executions` |
| `status()` | `status()` | `GET` |
| `execution_status(execution)` | `execution_status(execution=None)` | `GET /executions/{n}` |
| `read_output(execution, stream, line_range, max_chars)` | the same | `GET /executions/{n}/output` |
| `search_output(query, execution, stream, query_mode, context_before, context_after, ignore_case, max_chars)` | the same | `GET /executions/{n}/search` |
| `wait(execution, timeout_seconds)` | the same | `POST /executions/{n}/wait` |
| `interrupt()` | `interrupt()` | `POST /interrupt` |
| `restart()` | `restart()` | `POST /restart` |

The parameters are the tools' and mean what they mean there: `execution=None`
selects the current execution, then the most recent, and appears in a path as
`latest`. A cell header's `--wait` is for the MCP call; a program states its
wait as `wait_seconds`. Each method returns the structured result the session
gives, which is what a structured MCP result carries.

One more read belongs to a kernel: `follow(execution, offset, wait=0.5)`, the
combined output after `offset` characters, answered as soon as new text exists.
It is how a program receives output while a cell runs, which a tool result does
for an agent.

An execution number is the coordinate of an execution, as it is for the
tools; no object stands for one.

## 5. Names

### 5.1 Paths

| Written | With a context | Without one |
| --- | --- | --- |
| an id | that kernel | that kernel |
| `child` | the kernel of the context's scope with that name: its main kernel or one of its child kernels | the main kernel with that name |
| `/main` | the main kernel `main` | the same |
| `main/child`, `/main/child` | the child kernel `child` of `main` | the same |

A context's scope is its main kernel and that main kernel's child kernels; a
child kernel's context is the scope of its main kernel. A name with `/` in it
is read from the root, with or without the leading `/`, because scopes are one
level deep and a relative path of two parts could name nothing.

Who the caller is:

| Caller | Context | Where it lives |
| --- | --- | --- |
| an MCP session | its main kernel | the daemon's lease table, by MCP session id; the lease is the binding |
| code in a kernel | that kernel | `LOOMMUX_KERNEL_ID` in the kernel's environment, written by the daemon when it started the kernel |
| code outside any kernel | none | |

So `%%loommux --kernel build` and `loommux["build"]` read `build` the same
way, from the caller's main kernel.

### 5.2 No `/` in a name

A name containing `/` would be read as two names. `create`, `rename`, a
connection header and `--attach --create` refuse it. Generated names,
`private-…` and numbered `name-2`, never contain one.

### 5.3 Finding and opening

`daemon[path]` finds and never opens. A path naming nothing answers
`kernel_not_found` with the names that path's scope has.

`create(path)` returns the kernel the path finds, and otherwise opens it. It
opens only child kernels: `child` in the context's main kernel, or
`main/child` in an existing main kernel. A main kernel lives while a session
holds a lease on it, so only a session opens one, with
`%%loommux --attach NAME --create`; `create("/name")` of a missing main kernel
says so. A child kernel opened from a child kernel joins the same main kernel.

Two callers creating one name at once get one kernel: the second joins the
start of the first.

### 5.4 Names change, ids do not

A kernel renamed for a new task keeps its id. A path is for the hand that
writes it now; code that keeps a reference keeps the id, which `kernels()`
shows beside the name.

## 6. Forwarding, Written Out

IPython turns `%%loommux --kernel build` into
`get_ipython().run_cell_magic('loommux', '--kernel build', body)`. The magic
uses only the two panels:

```python
target = loommux.create("build")               # the daemon's: find or open
n = target.run_cell(body, wait_seconds=0)["execution"]
offset = 0
while True:                                    # the kernel's: follow its output
    state = target.follow(n, offset)
    write(state["text"]); offset = state["offset"]
    if state["status"] != "running":
        break
```

and, when this cell is interrupted while it waits, `target.interrupt()`
before it ends. The cell is waiting on `build` in the foreground, as
`subprocess.run` waits on a child process and kills it when interrupted, so
interrupting the one interrupts the other. A program that does not want to wait
calls `run_cell` with `wait_seconds=0` and keeps the execution number.

## 7. Removed

| Removed | Why |
| --- | --- |
| `Job` | an execution number is the coordinate, as in the tools; `Job.interrupt()` interrupted whatever the kernel ran, not the job |
| `Kernel.run`, `Job.output`, `Job.wait`, `Job.status` | the eight operations have the tools' names |
| `Kernel.recycle`, `Kernel.rename` | death and names are the registry's |
| `loommux[name]` opening a child kernel | finding and opening are two acts |
| `kernels()` inside a kernel listing one scope; `?home=` | names are scoped by construction, so the view need not be |
| `POST /api/resources/ensure` | split into lookup and create |

## 8. Rejected Alternatives

| Alternative | Why not |
| --- | --- |
| Keep `recycle` and `rename` on the kernel handle, as PyGithub's `repo.delete()` | a handle is for observing and controlling one kernel; what changes the registry belongs to the one who keeps it |
| Look a bare name up in the caller's scope, then among main kernels | when a child kernel and another session's main kernel share a name, the answer would depend on the order of the search |
| Read `main/child` without a leading `/` as relative | a relative path of two parts can name nothing |
| Let `create` open a main kernel | nothing would hold it: a main kernel lives by a session's lease |
| Keep a `Job` object beside execution numbers | two coordinates for one execution, and the object's methods drifted from the tools' |

## 9. Verification

| ID | Behavior | Proved by |
| --- | --- | --- |
| C1 | lookup reads ids, bare names in the context's scope or at the root, and `/main/child` from the root; it never opens | `tests/resource/test_manager.py` |
| C2 | `create` opens a child kernel in the context's main kernel or in `main/child`, returns an existing one, refuses a main kernel, and two at once open one | `tests/resource/test_manager.py` |
| C3 | a name with `/` is refused | `tests/resource/test_manager.py`, `tests/mcp/test_control.py` |
| C4 | `kernels()` lists every kernel from inside a kernel | `tests/mcp/test_kernel_control.py` |
| C5 | a kernel handle's eight operations reach that kernel, with `execution=None` meaning current or latest | `tests/mcp/test_kernel_control.py` |
| C6 | `rename` and `recycle` on the daemon take a path, an id or a handle | `tests/mcp/test_kernel_control.py` |
| C7 | forwarding uses `create`, `run_cell`, `follow` and `interrupt` | `tests/kernel/test_kernel_extension.py`, `tests/mcp/test_kernel_control.py` |
