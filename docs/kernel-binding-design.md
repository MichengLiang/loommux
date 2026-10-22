# Main Kernel Binding Design

> **Status: Implemented on October 23, 2026.** This document owns how an MCP
> session chooses its kernel, how it returns to one later, how kernels are
> named, and how long a kernel waits for its session. The
> [Kernel Resource Daemon Design](kernel-resource-daemon-design.md) keeps the
> lease mechanics and the control plane; the
> [Cell Header Design](cell-header-design.md) keeps the header grammar and
> forwarding.

## 1. Decision

Every MCP session works in exactly one **main kernel**. A main kernel has a
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

A session chooses its main kernel on its first call, and may switch later:

| Cell header | First call of a session | Later |
| --- | --- | --- |
| none | open a main kernel named `private-<session>` | the session's main kernel |
| `%%loommux --kernel NAME` | open a main kernel `NAME`, or take back a main kernel `NAME` that no session is connected to | forward the cell to the child kernel `NAME` |
| `%%loommux --attach NAME` | work in the existing main kernel `NAME` | let go of the current main kernel and work in `NAME` |

A main kernel is kept while a session that works in it stays connected, and
for 24 hours after the last call once none does. A kernel renames itself with
one line, `loommux.self.rename("new-name")`, inside any cell.

## 2. Journeys

The design is derived from what a coding agent does across the life of its
host. Each journey names the only rule it needs.

| Journey | What the agent writes | Rule needed |
| --- | --- | --- |
| Start a task | first cell begins `%%loommux --kernel 主kernel绑定设计` | the first `--kernel` names the session's main kernel |
| Start without thinking about it | an ordinary first cell | an unnamed session gets `private-<session>` |
| Its name was taken | the first result says `主kernel绑定设计-2` | a taken name is numbered, never refused |
| Work | ordinary cells; `%%loommux --kernel build` for a side kernel | after the first call, `--kernel` forwards |
| The host closes, a new host resumes the conversation | first cell begins `%%loommux --kernel 主kernel绑定设计` or `--attach` | a main kernel no session is connected to is taken back |
| The first cell ran before remembering | `%%loommux --attach 主kernel绑定设计` | `--attach` switches at any time; the kernel let go of is released |
| The task changed direction | `loommux.self.rename("新的任务名")` inside a cell | a kernel renames itself |
| The person running loommux looks after everything | the console at `/` | every kernel stays visible and controllable there |

## 3. Identities

The daemon design separates MCP session, client lease, logical resource, and
kernel process. This design adds no identity; it gives one of them a new role.

### 3.1 The lease is the binding

A session's main kernel is the resource that holds that session's client
lease. Child kernels never hold client leases, so a session holds at most one
lease, on at most one main kernel. There is no separate session-to-kernel
table.

A second table would state the same fact twice, and the two copies could
disagree: a table entry pointing at a kernel whose lease the sweep already
released, or a lease no entry points at. With the lease as the binding, every
rule that already releases a lease also unbinds the session, and every rule
that already keeps a lease keeps the binding.

### 3.2 Main kernels and homes

A resource records its `home`: `None` for a main kernel, the main kernel's
resource id for a child kernel. A child kernel opened from another child kernel
joins the same home: a home has one level. The home never changes after the
kernel starts, so a child kernel stays a child kernel even while its main kernel
is being retired.

A child kernel is anchored exactly when its main kernel is anchored, and a main
kernel is anchored exactly while it holds a client lease. This replaces the
earlier holder set and its reachability walk: holders could only form a graph
because any kernel could open any named kernel. Once names are scoped to a
home, every child kernel has one holder, its main kernel, and cycles cannot
form.

One level is enough because child kernels exist to run something beside the
main kernel, such as tests or a long computation; long-running processes are
usually supervised processes rather than kernels. A deeper tree would add
ownership questions that no journey asks.

## 4. Choosing the main kernel

### 4.1 The first call

The daemon reads a `run_cell` header before it selects a resource, so the
first call can choose by name before any kernel is allocated. A session's first
call is any call made while it holds no lease; the daemon therefore also knows
when a session's lease has been released and the session starts over.

| First call carries | The named main kernel | Result |
| --- | --- | --- |
| nothing | — | open `private-<first 8 characters of the session id>` |
| `--kernel NAME` | does not exist | open `NAME` |
| `--kernel NAME` | exists, no session connected to it | take it back |
| `--kernel NAME` | exists, a session is connected to it, or it is starting | open `NAME-2`, or the next free number |
| `--attach NAME` | exists | work in it, whether or not other sessions do |
| `--attach NAME` | does not exist | `kernel_not_found`; no kernel is opened and no execution is allocated |
| an `X-Loommux-Resource: NAME` connection header | either | work in `NAME`, opening it if it does not exist |

A session is **connected** to a main kernel when its lease there has any
standing but `disconnected`. Under activity and heartbeat leases the daemon
cannot observe departure, so every unexpired lease counts as connected and
`--kernel` takes nothing back; `--attach` still does.

`--kernel NAME` on the first call is not a session default added to the
per-cell grammar. A header without `--kernel` means the kernel that receives
the cell, and a header naming the receiving kernel's own name means the same
(Cell Header Design H5). On a session's first call no kernel has received
anything yet, so the name it writes chooses which kernel that is; every later
`--kernel` with the same name is H5 again, and every other name is a forward.
No header changes the meaning of a later cell.

A connection header settles the session's main kernel before its first call,
so with one, a first `--kernel` is a forward like any later one.

### 4.2 Numbering instead of refusing

The first call is also the call that does the agent's work. A refusal would
waste that call and make the agent write the cell again only to change a name.
The name the agent writes describes its task in a phrase, so a collision is
rare and harmless; numbering resolves it without a second round trip, and the
result reports the name actually given. The first kernel keeps the bare name
and the number counts how many kernels asked for it, the convention people
already know from file managers.

Numbering considers main kernels that are still starting, so two sessions
opening the same name at the same moment receive `NAME` and `NAME-2`, not one
shared kernel. Taking back is decided again when the lease is recorded: of two
sessions taking back one waiting kernel at the same moment, the second finds
the first connected and is numbered.

The kernel that receives the first cell still sees `--kernel NAME` in it while
its own name is `NAME-2`. It is therefore told the requested name at start, and
its header reader treats that name as its own until the kernel is renamed;
otherwise the first cell would be forwarded to a new child kernel called
`NAME`.

### 4.3 Attaching later

`--attach NAME` names the main kernel the session works in from this cell on.
It is evaluated on every call that carries it:

- `NAME` matches a main kernel by name or by resource id; child kernels are not
  candidates, because they belong to their home.
- The session's lease on its previous main kernel is released at once. That
  kernel is retired by the next sweep if no other session holds it, and its
  child kernels with it; a running cell receives the ordinary orphan grace.
- Attaching to the current main kernel changes nothing.
- The cell then runs in `NAME`; its execution numbers continue that kernel's
  sequence.

Attaching lets go immediately because it is an explicit choice. The 24-hour
window exists for kernels whose session went away without saying so; a kernel
the agent has just exchanged for another is not one of them.

`--attach` never opens a kernel. The only reason to attach is to return to a
kernel that exists, so a missing name is a fact the agent must learn, for
example that the kernel was retired after its window or that the server
restarted; an empty kernel opened in its place would hide that. The refusal
lists the main kernels no session is connected to.

`--attach` is read only by the daemon, when an MCP session submits a cell:
switching is a property of the session, not of a kernel. A kernel that receives
`--attach` naming itself treats the header as its own; naming any other kernel,
for example in a forwarded cell or one submitted through the control plane, is
a `UsageError`. `--attach` and `--kernel` cannot appear in one header.

### 4.4 Joining by connection header

`X-Loommux-Resource` predates this design and keeps its meaning: every session
configured with the same name works in one shared main kernel. Several sessions
in one main kernel need no new rule, because a resource has always held one
independent lease per session; it is kept while any of them holds.

## 5. Names

Every name follows the existing label rule: printable, at most 256 characters.
A cell header splits its line with shell-word rules, so a name with spaces needs
quotes; a task phrase without spaces needs none.

| Kind | Unique among | Assigned by |
| --- | --- | --- |
| main kernel | all main kernels, including ones still starting | first call, connection header, rename |
| child kernel | the child kernels of one home | `loommux["name"]`, `%%loommux --kernel name`, rename |

`loommux.self.rename(name)` renames the kernel that runs it and returns the
name it received; a taken name is numbered as on the first call. It is one line
in a cell that does other work, so changing a name never costs a call. A kernel
knows its own current names at once, so the next header naming it is read as
its own. A rename made from elsewhere, such as from its main kernel, reaches the
kernel the next time the daemon describes it to the kernel: a listing, a
lookup, its status. A header using the new name before that is resolved by the
daemon to this very kernel, which cannot run the body inside itself; the
header is reported as naming this kernel, and the cell run again is the
kernel's own.

Inside a home the main kernel and the asking kernel resolve before child
kernels, so a rename never gives a kernel a name another kernel of its home
answers to: a child kernel named like its main kernel, or a main kernel named
like one of its child kernels, would hide the other. Such a name is numbered
too.

A renamed `private-…` main kernel counts as named for the activity window
below.

## 6. Lifetime

| Kernel | Kept while | Retired |
| --- | --- | --- |
| main kernel | a session's lease holds it | when its last lease is released and no work runs; a running cell first receives the orphan grace |
| child kernel | its main kernel is kept | with its main kernel |

A connection lease is held while the session's stream is open. After the
stream closes and the one-minute reconnect grace passes, the lease falls back to
the named activity window, 24 hours by default
(`LOOMMUX_NAMED_TTL_SECONDS`), counted from the lease's last renewal, and
raises no alert. A renewal is a request, or the end of an operation the
session was waiting for, so a computation left running when the host closed
is kept for a day after it finished, when its result is there to come back
to. Any call by any session renews its own lease, so a main kernel is retired
24 hours after the last call of the last session that used it.

The window protects the person running loommux from kernels they forgot, not
the agent. Hosts are closed by that person, who knows which kernels they want
and can retire any of them from the console at any time. One window, the same
for every main kernel, is therefore enough: an unnamed kernel is as easy to
forget and as easy to return to (its name is in the first result) as a named
one. The named window is reused because it already meant "how long a workbench
that sessions come back to by name waits for them".

Activity and heartbeat leases are unchanged: an unnamed main kernel uses the
private activity window, every other main kernel the named one, as private and
named resources did before.

## 7. What a kernel sees

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
address every kernel by id.

## 8. What the agent is told

A call that binds or switches the session's main kernel ends with one text
block naming the kernel and how it was chosen, after any output and before a
lease alert:

```text
Kernel: 主kernel绑定设计-2 (opened; 主kernel绑定设计 is in use by another session)
Kernel: 主kernel绑定设计 (taken back; 12 earlier executions)
Kernel: 主kernel绑定设计 (attached; 12 earlier executions; let go of private-1a2b3c4d)
Kernel: private-1a2b3c4d (opened)
```

It is a separate block so that the execution's own text, and every rule about
it, stays exactly as before. It appears once, when the binding changes: the
agent needs the name at that moment, and repeating it in every result would add
nothing to what it already read. `status` reports the current name on its
first line, for an agent that has lost track of it after its context was
compacted.

## 9. Rejected Alternatives

| Alternative | Reason |
| --- | --- |
| Refuse `--attach` to a main kernel another session is connected to | The rule assumed a main kernel belongs to one session. A session has exactly one main kernel; the converse never held, and the lease set has always kept a resource for several sessions |
| Keep a kernel only after the agent asks, `keep()` | The agent cannot ask at the moment it would need to: the host is closed from outside |
| A short window for unnamed kernels and a long one for kept kernels | Two windows for one purpose; the window protects the owner, who forgets either kind equally |
| Prefix every `In [n]:` with the kernel name | The agent reads each input beside its output and works in one kernel at a time; the name matters only when the binding changes |
| Refuse a taken name on the first call | Wastes the call that does the work; numbering resolves it in place |
| `--attach` opens a missing kernel | Hides that the kernel is gone |
| Global child kernel names | Two agents following the documentation's own `build` example shared one kernel, its namespace, and its lifetime |
| A session-to-kernel table beside the leases | Two records of one fact |
| Make the first `--kernel` the only way to bind | Leaves no way back after a first cell that ran before the agent remembered its kernel |
| Remove `X-Loommux-Resource` | Nothing in the new model contradicts several sessions sharing one main kernel |

## 10. Verification

| ID | Proof |
| --- | --- |
| B1 | A session's first call without a name opens `private-<session>`; its calls keep using it |
| B2 | A first `--kernel NAME` opens `NAME`, and the same header later is the kernel's own |
| B3 | A first `--kernel NAME` takes back a main kernel no session is connected to, with its namespace and execution sequence |
| B4 | A first `--kernel NAME` while `NAME` is connected, or starting, opens `NAME-2`; the first cell runs there and is not forwarded |
| B5 | `--attach NAME` switches at any time; the previous main kernel and its child kernels are retired by the next sweep |
| B6 | `--attach` to a missing name opens nothing, allocates no execution, and lists the main kernels waiting to be taken back |
| B7 | `--attach` naming another kernel inside a kernel is a `UsageError`; with `--kernel` in one header it is a header error |
| B8 | Two homes each open `build`; they are different kernels, and neither home lists the other's |
| B9 | A connection lease whose stream closed keeps its main kernel for the named window after its last renewal, without an alert |
| B10 | `loommux.self.rename()` renames, numbers a taken name, and the next header with the new name is the kernel's own |
| B11 | A binding change ends the result with one `Kernel:` block; `status` shows the name |
| B12 | `scripts/stress.py` passes |

B1 to B11 are proved by `tests/resource/test_manager.py`,
`tests/resource/test_leases.py`, `tests/resource/test_liveness.py`,
`tests/kernel/test_kernel_extension.py`, `tests/test_cell_header.py`,
`tests/mcp/test_presentation.py`, and, against a real HTTP daemon,
`tests/mcp/test_kernel_binding.py`. The load check's cell mix opens main
kernels by name on first cells, forwards to child kernels, and attaches.
