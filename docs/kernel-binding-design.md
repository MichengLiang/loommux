# Main Kernel Binding Design

> **Status: Revised on October 23, 2026.** This document owns how an MCP
> session chooses the kernel it works in, how it returns to one later, how
> kernels are named, and how long a kernel waits for a session. The
> [Kernel Resource Daemon Design](kernel-resource-daemon-design.md) keeps the
> lease mechanics and the control plane; the
> [Cell Header Design](cell-header-design.md) keeps the header grammar and
> forwarding. The first version of this design, implemented the same morning,
> chose a session's kernel by what its first call said; section 11 records
> what that version did and why it was replaced.

## 1. Decision

Every MCP session works in one **main kernel** at a time. A main kernel has a
name that is unique among main kernels. A kernel that a kernel opens by name is
a **child kernel** of that kernel's main kernel: its name is unique only inside
that **home**, it lives as long as its main kernel, and kernels of other homes
neither see nor reach it.

```text
person running loommux      every kernel, through the console and the control plane
└── MCP session             works in one main kernel at a time
    └── main kernel         "loommux-binding-design"
        ├── child kernel    "build"
        └── child kernel    "lab"
another MCP session
└── main kernel             "report-cleanup"
    └── child kernel        "build"         (a different kernel from the one above)
```

The cell header has one option for each of two intents, and each means the
same thing in every cell:

| Header | Meaning |
| --- | --- |
| none | run in the session's main kernel; a session that has none yet opens `private-<session>` |
| `%%loommux --attach NAME` | from this cell on, the session works in the main kernel `NAME`, letting go of the one it had |
| `%%loommux --attach NAME --create` | the same, opening `NAME` when no main kernel has that name |
| `%%loommux --kernel NAME` | this one cell runs in the child kernel `NAME` of the session's home, opened on first use |

A main kernel lives while a client is connected to it. Once none is, a named
main kernel is kept for 24 hours after the last call, and an unnamed
`private-…` one is retired: giving a kernel a name is what asks for it to be
kept. A kernel renames itself with one line, `loommux.self.rename("new-name")`,
inside any cell.

## 2. Where The Shape Comes From

A named workspace that outlives the client attached to it is an old journey.
Terminal multiplexers, notebook servers, and container runtimes each walked it
for decades, independently, and arrived at the same rules. Loommux takes those
rules as they are; every row below is one of them.

| Rule | Prior art | Here |
| --- | --- | --- |
| Every workspace has a name; one is generated when none is given | tmux numbers sessions `0`, `1`, …; Docker names containers like `eloquent_turing` | `private-<session>` |
| A client leaving is not an event the workspace reacts to | tmux `detach`; closing a Jupyter browser tab | a departed session's lease is simply released |
| Several clients may attach to one workspace | several tmux clients on one session; several Jupyter frontends on one kernel | `--attach` never asks who else is there |
| Attach finds by name and does not create; creating is an explicit switch on the same command | `tmux new-session -A -s NAME`, `screen -R NAME`, `zellij attach --create NAME` | `--attach NAME`, `--attach NAME --create` |
| Where the client lives and where one command runs are separate | tmux `-t TARGET`, `docker exec NAME CMD` beside `docker attach` | `--attach` beside `--kernel` |
| A workspace can be renamed | `tmux rename-session`, `docker rename` | `loommux.self.rename()` |
| An idle workspace nobody is connected to is culled after a timeout; activity resets it | Jupyter Server `cull_idle_timeout`, with `cull_connected = False` | a named main kernel 24 hours after the last call, never while connected |

Two consequences follow before any loommux-specific reasoning. A cell header
that meant "choose my workspace" on one call and "run elsewhere" on the next
would be the one tool in the family whose options depend on history. And a
workspace that keeps a record for every client that ever left it would be the
one tool whose bookkeeping grows with how often its owner opens and closes
windows.

## 3. Journeys

| Journey | What the agent writes | Rule needed |
| --- | --- | --- |
| Start a task | first cell begins `%%loommux --attach 主kernel绑定设计 --create` | `--create` opens a missing name |
| Start without thinking about it | an ordinary first cell | a session without a main kernel opens `private-<session>` |
| Close a throwaway host | nothing | an unnamed main kernel is retired once its session left |
| Work | ordinary cells; `%%loommux --kernel build` for a side kernel | `--kernel` always forwards one cell |
| The host closes; a new host resumes the conversation | the same first cell, `%%loommux --attach 主kernel绑定设计 --create` | `--attach` finds the kernel by name, whoever is or was connected |
| A cell ran before the agent remembered its kernel | `%%loommux --attach 主kernel绑定设计` | `--attach` works on any call; the kernel let go of is released |
| The task changed direction | `loommux.self.rename("新的任务名")` inside a cell | a kernel renames itself |
| The person running loommux looks after everything | the console at `/` | every kernel stays visible and controllable there |

The agent writes the same first cell whether the kernel is new or waiting for
it. It need not know which, and the result tells it (section 9).

## 4. Identities

The daemon design separates MCP session, client lease, logical resource, and
kernel process. This design adds no identity; it settles which of them stands
for "who".

### 4.1 The name is the key; a session is a pipe

An MCP session is one connection between one host process and the daemon.
Claude Code opens a new session whenever its transport reconnects, and a
resumed conversation runs in a new host process with a new session; the agent
in it is the same agent with the same context, and cannot even observe that
the pipe changed. The thing that persists across all of this is the name in
the agent's context. Whoever writes the name works in the kernel, the way
whoever holds a key opens the door.

The daemon therefore never asks which session a kernel "belongs to". It does
not refuse a kernel another session is connected to, does not number a name
because a session is connected, and keeps nothing about sessions that left.

Two different agents choosing exactly the same task phrase would share one
kernel. The phrase describes a task, so this is rare; when it happens it is
visible in `loommux.kernels()` and the console, and either agent renames its
way out. Guarding against it would mean telling a stranger from the same agent
on a new pipe, which nothing the daemon observes can do.

### 4.2 The lease is the binding

A session's main kernel is the resource that holds that session's client
lease. Child kernels never hold client leases, so a session holds at most one
lease, on at most one main kernel. There is no separate session-to-kernel
table: a second record of one fact could disagree with the leases the sweep
releases. Every rule that releases a lease also unbinds the session, and a
session whose lease was released simply has no main kernel until its next call.

### 4.3 Main kernels and homes

A resource records its `home`: `None` for a main kernel, the main kernel's
resource id for a child kernel. A child kernel opened from another child kernel
joins the same home: a home has one level. The home never changes after the
kernel starts, so a child kernel stays one even while its main kernel is being
retired. Every child kernel has exactly one holder, so cycles cannot form.

One level is enough because child kernels exist to run something beside the
main kernel, such as tests or a long computation; long-running processes are
usually supervised processes rather than kernels.

## 5. Choosing The Main Kernel

The daemon reads a `run_cell` header before it selects a resource, so a cell
can name its main kernel before any kernel is allocated.

| The call carries | The session | `NAME` | Result |
| --- | --- | --- | --- |
| no `--attach` | has a main kernel | — | that kernel |
| no `--attach` | has none | — | open `private-<first 8 characters of the session id>` |
| `--attach NAME` | either | exists | work in it |
| `--attach NAME` | either | does not exist | `kernel_not_found`; nothing is opened and no execution is allocated |
| `--attach NAME --create` | either | does not exist | open `NAME` and work in it |
| an `X-Loommux-Resource: NAME` connection header | has none | either | work in `NAME`, opening it if needed |

`--attach NAME` matches a main kernel by name or by resource id. Child kernels
are not candidates: they belong to their home.

When the session had another main kernel, its lease there is released at once.
That kernel is retired by the next sweep unless another session holds it, and
its child kernels with it; a running cell receives the ordinary orphan grace.
Letting go is immediate because switching is an explicit act: the 24-hour
window exists for kernels nobody said anything about (section 7). Attaching to
the current main kernel changes nothing, so the agent may repeat its first-cell
header at any time.

`--attach` without `--create` never opens a kernel, as `tmux attach` never
does: an agent that means "my kernel from before" must learn that it is gone,
for example retired after its window or lost with a server restart, rather than
silently receive an empty one. The refusal lists the existing main kernels and
suggests `--create`. With `--create` the agent says that a new kernel is fine,
which is what a first cell means.

Two sessions opening one name at the same moment share one provisioning task
and receive one kernel: the name is the key (section 4.1).

`--attach` is read only by the daemon, when an MCP session submits a cell; the
session is what moves. A kernel that receives `--attach` naming itself treats
the header as its own, because the daemon has already moved the session there.
One naming any other kernel, in a forwarded cell or one submitted through the
control plane, is a `UsageError`.

`--kernel` never chooses the main kernel. On a session's first call it forwards
the cell to a child kernel of the session's new `private-…` kernel, exactly as
it would on any other call.

`X-Loommux-Resource` predates this design: every session configured with the
same name works in one shared main kernel. It is `--attach NAME --create`
written once in the connection's configuration, consulted while the session has
no main kernel; an `--attach` in a cell still moves the session.

## 6. Names

Every name follows the existing label rule: printable, at most 256 characters.
A cell header splits its line with shell-word rules, so a name with spaces needs
quotes; a task phrase without spaces needs none.

| Kind | Unique among | Assigned by |
| --- | --- | --- |
| main kernel | all main kernels, including ones still starting | `--attach NAME --create`, connection header, rename |
| child kernel | the kernels of one home | `loommux["name"]`, `%%loommux --kernel name`, rename |

`loommux.self.rename(name)` renames the kernel that runs it and returns the
name it received. A name another kernel already has is numbered, `name-2`,
because two kernels may not answer to one name; renaming is the only place
numbering remains. Inside a home the main kernel and the asking kernel resolve
before child kernels, so a name another kernel of the home answers to is
numbered too: a child kernel named like its main kernel would hide one from the
other.

A kernel knows its own current name at once after renaming itself, so the next
header naming it is read as its own. A rename made from elsewhere, such as from
its main kernel, reaches the kernel the next time the daemon describes it to
the kernel: a listing, a lookup, its status. A header using the new name before
that is resolved by the daemon to this very kernel, which cannot run the body
inside itself; the header is reported as naming this kernel, and the cell run
again is the kernel's own.

A renamed `private-…` main kernel counts as named for the activity window.

## 7. Lifetime

| Kernel | Lives while | Retired |
| --- | --- | --- |
| named main kernel | a client lease holds it, or less than the named window has passed since the last call of the last session that left it | when neither holds; a running cell first receives the orphan grace |
| unnamed main kernel (`private-…`, never renamed) | a client lease holds it | when its last lease is released |
| child kernel | its main kernel lives | with its main kernel |

For a named main kernel this is Jupyter's culling rule with
`cull_idle_timeout` set to the named window, 24 hours by default
(`LOOMMUX_NAMED_TTL_SECONDS`), and `cull_connected = False`.

A connection lease is held while its session's stream is open. When the stream
closes, no replacement arrives within the reconnect grace, and the client sent
nothing after the close, the session has left and its lease is released. At that
moment a named main kernel records its own **retention**: the lease's last
renewal plus the named window, or the later of that and a retention it already
had. A renewal is a request, or the end of an operation the session was
waiting for, so a call still running when the host closed counts from when it
finished. An unnamed main kernel records none and is retired by the same sweep,
as every main kernel was before this design.

Retention has exactly one consumer: a later session that attaches to the kernel
by name. A name the agent chose describes its task and is in its context; that
is what a later host writes in its first cell. A generated `private-…` name is
a slice of a session id that the agent never chose and does not carry forward,
so no session will attach to it, and keeping the kernel would only hold its
memory for a day, once for every throwaway host the owner closes. Naming is
therefore the request to be kept: `--attach NAME --create` in the first cell
asks for it at the start, when the agent can still ask, and
`loommux.self.rename()` asks for it later. The same `named` flag already chose
the 24-hour rather than the 80-minute window under activity leases; here it
chooses between keeping and not keeping.

The fact "how long this kernel waits" belongs to the kernel, so it is stored
once, on the kernel. Keeping it on each departed session's lease would store
one fact once per departure, show the owner one row per closed window, and
make "is anyone here" a question that has to skip records of people who are
not.

The window protects the person running loommux from kernels they forgot, not
the agent. That person closes the hosts, knows which kernels they want, and can
retire any kernel from the console at any time; a computer left running with
its hosts open keeps every kernel, because connected kernels never count down.
So one window serves every named main kernel, and a kernel that is busy when its
retention ends is retired like an idle one after the orphan grace: a forgotten
runaway cell is exactly what the window is for. Jupyter protects busy kernels by
default (`cull_busy = False`); its window protects work, this one protects the
owner.

Switching away (section 5) releases the session's lease and clears the kernel's
retention, so the kernel is retired unless another session holds it.

Under activity and heartbeat leases the daemon cannot observe departure: the
lease's own renewal window is the only judgment of whether the client is still
there, so it is released when that window ends and no retention follows. An
unnamed main kernel uses the private activity window, every other main kernel
the named one, as before.

The reconnect grace no longer shapes any kernel's lifetime: a kernel kept for a
day does not care whether its session left now or a minute from now. It still
decides when a missing stream becomes a lease alert.

## 8. What A Kernel Sees

| Expression, inside a kernel | Meaning |
| --- | --- |
| `loommux.kernels()` | this home: its main kernel and child kernels |
| `loommux["build"]` | the child kernel `build` of this home, opened on first use; this home's main kernel or this kernel by its own name or id |
| `loommux.self.rename(name)` | rename this kernel |

Outside a kernel, `loommux.daemon.Daemon()` acts for the person running
loommux: `kernels()` lists every kernel, and `daemon[name]` resolves a main
kernel by name or any kernel by id. It opens nothing, because nothing outside a
kernel can hold a new kernel.

Scoping is a view, not a permission: the control plane and the console still
address every kernel by id. Snapshots report a main kernel's remaining
retention, which the console and `kernels()` show beside its live leases.

## 9. What The Agent Is Told

A call that changes the session's main kernel ends with one text block naming
the kernel and how it was reached, after any output and before a lease alert:

```text
Kernel: 主kernel绑定设计 (opened)
Kernel: 主kernel绑定设计 (attached; 12 earlier executions)
Kernel: 主kernel绑定设计 (attached; 12 earlier executions; let go of private-1a2b3c4d)
Kernel: private-1a2b3c4d (opened)
```

It is a separate block so that the execution's own text, and every rule about
it, stays exactly as before. It appears once, when the main kernel changes: the
agent needs the name at that moment, and repeating it in every result would add
nothing to what it already read. `status` reports the current name on its first
line, for an agent that has lost track of it after its context was compacted.

## 10. Rejected Alternatives

| Alternative | Reason |
| --- | --- |
| A separate `keep()` call, made when the agent wants to keep its kernel | The agent cannot ask at the moment it would need to, because the host is closed from outside; naming in the first cell asks at the start, and a second act would only be one more thing to forget |
| Keep unnamed kernels too, for the same window | Retention serves a session returning by name, and nobody returns to a generated session-id name; it would hold a day of memory for every throwaway host and make naming mean nothing (section 11) |
| Prefix every `In [n]:` with the kernel name | The agent reads each input beside its output and works in one kernel at a time; the name matters only when it changes |
| `--attach` opens a missing kernel by itself | Hides that the kernel is gone; tmux, screen, and zellij all keep creation an explicit switch |
| Spare a busy kernel when its retention ends | The window guards the owner against forgotten work, runaway cells included |
| New option names such as `--main` and `--child` | Invented words where the family already has `attach` and a per-command target |
| Global child kernel names | Two agents following the documentation's own `build` example shared one kernel, its namespace, and its lifetime |
| A session-to-kernel table beside the leases | Two records of one fact |
| Remove `X-Loommux-Resource` | Several sessions sharing one main kernel is the ordinary case, not an exception |

## 11. The First Version

The first version of this design, implemented as round 10 and corrected as
round 11, made a session's first call special. Its rules and why each went:

| First-version rule | Why it went |
| --- | --- |
| On a session's first call `--kernel NAME` chose the main kernel; on later calls it forwarded | One option, two meanings, chosen by history: no cell could be read on its own |
| `--kernel NAME` on a first call opened `NAME-2` when a session was connected to `NAME` | Sessions are pipes (section 4.1). The common case it caught was the same agent reconnecting within the minute's grace, which then had to `--attach` its own kernel |
| A kernel numbered on its first call also answered to the name it had asked for | Existed only to make the previous rule's numbered kernels read their first cell; gone with it |
| A departed session's lease stayed, falling back to the named window, and showed as "waiting to be taken back" | Stored the kernel's retention once per departure; nothing read those leases except to skip them, and the console grew a row each time a window was closed |
| "Connected" meant any lease that had not departed, and decided taking back and numbering | Nothing needs to know any more |
| Every main kernel, named or generated, waited the same 24 hours, on the reasoning that a generated name is in the first result and so as easy to return to | It is in a result, but nobody carries it forward; the rule kept every throwaway kernel for a day and made naming pointless. Only named main kernels are kept now (section 7) |

The tests and documentation of that version proved its behavior, and the
behavior was what its rules said. What showed it wrong was using it: a resumed
host received `-2`, and the console listed three leases for one agent. The
family of tools in section 2 had answered both questions long before. The last
row was found the same way, by asking what a kept unnamed kernel is for.

## 12. Verification

| ID | Proof |
| --- | --- |
| B1 | A session's first call without `--attach` opens `private-<session>`; its later calls keep using it |
| B2 | `--attach NAME --create` opens `NAME` when missing and attaches when present; repeating it changes nothing |
| B3 | `--attach NAME` works on any call, including a kernel another session is connected to; the previous main kernel and its child kernels are retired by the next sweep |
| B4 | `--attach` to a missing name without `--create` opens nothing, allocates no execution, and lists the main kernels |
| B5 | `--kernel NAME` on a session's first call forwards to a child kernel of its new private kernel |
| B6 | `--attach` naming another kernel inside a kernel is a `UsageError`; `--create` without `--attach` is a header error |
| B7 | Two homes each open `build`; they are different kernels, and neither home lists the other's |
| B8 | A connection lease whose session left is released after the reconnect grace without an alert; its named main kernel stays for the named window after the lease's last renewal, and its snapshot reports the remaining retention |
| B9 | A kernel the session switched away from keeps no retention |
| B10 | `loommux.self.rename()` renames, numbers a taken name, and the next header with the new name is the kernel's own |
| B11 | A change of main kernel ends the result with one `Kernel:` block; `status` shows the name |
| B12 | `scripts/stress.py` passes |
| B13 | An unnamed main kernel whose session left is retired with the lease, and one renamed before it left is kept like any named kernel |

B1 to B11 and B13 are proved by `tests/resource/test_manager.py`,
`tests/resource/test_leases.py`, `tests/resource/test_liveness.py`,
`tests/kernel/test_kernel_extension.py`, `tests/test_cell_header.py`,
`tests/mcp/test_presentation.py`, and, against a real HTTP daemon,
`tests/mcp/test_kernel_binding.py`.
