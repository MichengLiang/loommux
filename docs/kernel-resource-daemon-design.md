# Kernel Resource Daemon Design

## 1. Purpose

A loommux server is a resource daemon. It owns logical IPython workbenches on
behalf of MCP participants and supervises the replaceable kernel processes that
currently execute them.

The four identities below are deliberately distinct:

```text
MCP session
    -> client lease
        -> logical KernelResource
            -> current IPython kernel process
```

An MCP session is a protocol participant. A client lease is that participant's
soft-state claim on one resource. A `KernelResource` is the stable workbench
identity and owns its `IPythonSession`, execution sequence, retained output, and
leases. The kernel PID is only the current process carrying the namespace.

This separation permits a kernel restart to replace a process without changing
the resource ID or discarding retained execution records.

## 2. Resource addressing

Every ordinary MCP tool call is routed before it reaches `IPythonSession`.

### Session-private resource

Without `X-Loommux-Resource`, the address is derived from the MCP session ID:

```text
session:<MCP session ID>
```

Separate MCP sessions therefore receive separate namespaces, execution
sequences, output histories, and kernel processes by default.

### Named shared resource

A client may send a URL-encoded `X-Loommux-Resource` header. Equal decoded
names select the same logical resource:

```text
named:<decoded resource name>
```

Each MCP session still receives an independent client lease. One participant
expiring does not retire the shared workbench while another valid lease
remains. A resource name is an address, not an authentication credential.

`X-Loommux-Operator` supplies an optional URL-encoded display label for the
participant.

## 3. Tool boundary

The model-facing surface remains exactly:

```text
run_cell
status
execution_status
read_output
search_output
wait
interrupt
restart
```

These tools operate on the resource selected for the current MCP connection.
They do not accept a resource ID because an execution integer is meaningful
inside its selected workbench.

Server-wide operations belong to the HTTP control plane instead of the model
tool list. Administrative execution coordinates are the pair
`(resource_id, execution)`.

## 4. Resource and process lifecycle

A logical resource moves through these states:

```text
running
orphaned
closing
stopped
```

A registered resource is `running` or `orphaned`; `closing` and `stopped`
follow its removal from the registry. The states record what the manager has
done with the resource, and only the manager's own operations change them.

Whether the current kernel process is alive is a separate fact at the next
identity layer. `IPythonSession` reports it, including whether a restart has
detached the old kernel and not yet installed the new one, and `/api/resources`
projects it as `kernel_state` (`running`, `restarting`, or `stopped`) beside
`kernel_pid`, which is `null` while no kernel process runs. The `status` tool
shows the same restart as `kernel: restarting`. Observers (the
sweep, console refreshes, and `health`) read that fact and never store it as
resource state: a restart leaves the session briefly without a kernel, and a
reading taken in that gap must not outlive it. A stored "crashed" reading once
outlived a successful restart and made the next tool call retry forever
without yielding the event loop, freezing the whole server.

`busy` is not a lifecycle state. It is derived from active control operations
or a running IPython execution.

Provisioning is deduplicated per resource address. Concurrent callers for one
address await the same startup task; unrelated addresses may start kernels in
parallel. Slow kernel readiness therefore does not hold the registry lock or
serialize every resource.

`restart` preserves the logical resource, lease set, execution records, and
next execution number while replacing the kernel process and namespace.
`recycle` removes the logical resource and closes its session.

Every operation that may replace the kernel process holds the resource's
lifecycle lock: the `restart` tool, the Windows `interrupt` tool (ipykernel
cannot receive Ctrl+C there, so the kernel is replaced), the control-plane
restart, and recovery of a dead kernel. Retirement holds the same lock. A
call that arrives during a replacement and finds no kernel therefore waits
for that replacement in recovery instead of starting a second kernel; of two
concurrent replacements, the kernel installed first is overwritten by the
other and never shut down.

If a kernel exits unexpectedly, its running execution is finished as `killed`
with a `KernelProcessExited` diagnostic. The resource becomes recoverable and
the next operation restarts the `IPythonSession` under the same resource ID.

Jupyter's local provisioner starts Unix kernels in a new session and signals
their process group during termination. Windows uses the loommux Job Object
containment. Tests require restart to terminate descendants on both supported
platform families.

POSIX kernels talk to the server over ZMQ IPC sockets inside the kernel's
private runtime root; Windows kernels use loopback TCP. Over TCP, Jupyter
chooses free ports before the kernel binds them, so another process can take
one first; IPC socket names belong to the private root and cannot be claimed.
Every kernel start, including restart and the Windows interrupt replacement,
retries once with a fresh process before it reports failure.

## 5. Lease policy

A lease retains one immutable policy generation:

```text
LeasePolicy
    mode
    generation
    private_activity_timeout_seconds
    named_activity_timeout_seconds
    heartbeat_interval_seconds
    heartbeat_timeout_seconds
    connection_grace_seconds
```

The current defaults may change, but an existing lease continues to use the
generation selected when it was created. Repeating an unchanged policy update
is idempotent and does not allocate a generation.

A lease-aware client discovers `/api/lease-policy` before MCP initialization
and sends the selected generation in
`X-Loommux-Lease-Policy-Generation`. This closes the race between policy
discovery and first resource use.

A lease does not store a deadline. It records renewal facts and
`resource/liveness.py` derives its standing on demand, so the sweeper, the
resource snapshot, and the tool-result alert read one decision:

```text
ClientLease
    renewed_at      every renewal, including operation completion
    requested_at    only client-originated traffic: tool-call entry and ping
```

`requested_at` alone is evidence that the client is alive; completion of a
long operation after the client left proves nothing about the client.

### Activity mode

Tool entry and completion renew the lease; it expires one activity window
after its latest renewal. Private and named resources use their
corresponding fixed activity timeout.

### Heartbeat mode

Tool activity and successful standard MCP `ping` refresh the heartbeat
deadline. The client sends ping at the advertised interval. The timeout must
be greater than the interval.

### Connection mode

A connection lease is held for as long as its MCP session keeps a standalone
server-push stream open: the Streamable HTTP `GET` stream that a client opens
after initialization, or the process's own standard streams for stdio. A
client that holds that stream for its whole lifetime turns "the stream is
open" into "the client is still there", without sending anything: when the
client process exits, its socket closes and so does the stream. Claude Code
2.1.303 behaves this way; it opens the stream right after
`notifications/initialized`, never pings while idle, does not send `DELETE`
on exit, and its stream closes within about a second of `/exit` or
`kill -9`.

`ConnectionRegistry` (`resource/connections.py`) records the transport facts
per session: when it was first seen, how many streams are open, when the
count last fell to zero, and whether the session is bound to the process
lifetime. `assess_lease()` turns them into one standing:

| Standing | Condition | Lease |
| --- | --- | --- |
| `connected` | a stream is open, or the session is bound to the process | held, no expiry |
| `awaiting_stream` | no stream yet, within grace of first sight | held while the grace runs |
| `reconnecting` | stream closed, within grace of the close | held while the grace runs |
| `disconnected` | closed, grace over, no request since the close | released |
| `unbound` | no stream ever, grace over | alert; activity window |
| `stream_lost` | closed, grace over, a request arrived after the close | alert; activity window |

The two alert standings mean the client's transport no longer behaves the
way this mode relies on. Such a lease degrades to the activity window of its
own policy generation instead of being released, so a client whose behavior
changed keeps working as it would under activity mode while the alert stays
visible. A later stream returns the lease to `connected`.

`connection_grace_seconds` defaults to 60. The MCP TypeScript SDK embedded in
Claude Code retries a dropped stream twice, 1 s and then 1.5 s apart, so a
minute absorbs a reconnect with wide margin while a closed host still
releases its kernel within about one sweep after that minute.

The registry forgets a session once it is disconnected and no lease refers
to it. A forgotten session that later calls a tool is judged as never having
opened a stream, which still alerts if no stream follows.

#### Transport observation

`mcp/stream_presence.py` installs an ASGI middleware on every HTTP
application the server builds; `run_http_async` builds its application
through the same `http_app`, so tests and the installed command observe
streams identically. A request counts as a standalone stream when it is a
`GET` carrying `Mcp-Session-Id` whose response starts with `200` and
`text/event-stream`; it stays open until that response ends. A rejected
`GET` (unknown session, a second concurrent stream) never reaches `200` and
is not counted. The observation relies only on ASGI and on what the MCP
Streamable HTTP specification defines, unlike the ping hook below.

A tool call whose MCP request context carries no HTTP request comes from
stdio or from an in-process client. Such a session lives exactly as long as
the server process, so it is bound to the process and stays `connected`.

The MCP session object behind a lease has its own lifetime in the SDK's
Streamable HTTP session manager, which loommux requires at `mcp>=1.30.0`: a
session closed with `DELETE` is deregistered at once, and any other session
is reaped after 30 minutes with no request in flight, where an open `GET`
stream counts as in flight. A connected client therefore keeps its session
however long it idles, and one that left without `DELETE` (Claude Code
sends none) is released half an hour after its stream closed. Earlier SDKs
kept every session for the life of the server.

#### Alerts

An alert standing surfaces in three places, each for a different reader:

- **Tool results.** After each tool call the server judges the caller's
  lease. When its standing is an alert, the tool result gains a final text
  block naming the standing, its cause, and the activity window now in
  force, and the structured status gains `lease_alert`. The person running
  loommux may never open the console, but the model reading the tool result
  will relay the alert to them; only the affected session sees it.
- **Server log.** The sweep logs one warning when a lease enters an alert
  standing, not on every pass; `ClientLease.reported_alert` remembers what
  was logged and clears when the lease stops alerting, so a later entry is
  logged again.
- **Console.** The lease-signal row turns red (section 7).

### Ping renewal

A successful standard MCP `ping` renews an existing lease under every mode
and counts as client-originated traffic.

Renewal attaches to the SDK's low-level ping request handler. FastMCP 3.x
answers `ping` before its middleware chain, so the handler registry is the only
supported place where a heartbeat is visible; the SDK's `request_handlers`
registry and FastMCP's `_mcp_server` are therefore pinned by
`mcp>=1.29.1,<2` and `fastmcp>=3.4.7,<4` in `pyproject.toml`. MCP 2.x renames
the registry and FastMCP 4.x routes `ping` through middleware, so both majors
require rewriting this hook rather than inheriting it.

Ping never creates a resource or missing lease. Kernel health observation is
also separate from client liveness and does not renew participation.

## 6. Expiry and orphaned execution

A sweep first removes leases whose standing is expired and whose client has
no active operation. It then evaluates resources with no remaining
participants and forgets disconnected sessions that no lease refers to.

An idle unowned resource is reclaimed immediately. A resource whose cell is
still running enters `orphaned`. This distinction matters because a loommux
`run_cell` request can return while the execution continues in the kernel.

An orphaned running resource receives `LOOMMUX_ORPHAN_GRACE_SECONDS`. If no
participant returns before the grace expires, the session is closed and the
kernel process group is terminated. This prevents an abandoned infinite cell
from retaining a resource forever.

A new valid operation arriving before retirement returns the resource to
`running` and attaches a fresh client lease.

## 7. HTTP control plane

The Streamable HTTP application serves:

| Method | Path | Meaning |
| --- | --- | --- |
| `GET` | `/` | Dependency-free resource console. |
| `GET` | `/api/resources` | Resource, execution, kernel, and lease snapshots. |
| `GET` | `/api/lease-policy` | Current policy generation. |
| `PUT` | `/api/lease-policy` | Atomically validate and update future defaults. |
| `POST` | `/api/resources/{id}/interrupt` | Interrupt the selected running execution. |
| `POST` | `/api/resources/{id}/restart` | Replace the selected kernel process. |
| `POST` | `/api/resources/{id}/health` | Observe kernel-process liveness without renewing a client lease. |
| `POST` | `/api/resources/{id}/recycle` | Recycle one resource; `force` permits busy retirement. |
| `POST` | `/api/resources/recycle-idle` | Recycle resources without active work. |
| `POST` | `/api/resources/recycle-all` | Force retirement of every resource. |

The control plane is operational infrastructure. It is not added to MCP
`tools/list`. It does not provide its own authentication layer; deployment
must keep it inside the same trusted boundary as arbitrary Python execution.

The console renders a lease-signal row above the policy panel on every
refresh. It names every lease in an alert standing and turns red, and is
otherwise a quiet line; because the row always exists, an alert recolors it
in place instead of pushing the resource cards down between two polls. Each
lease shows its standing next to its mode, and its remaining time as a
renewal countdown, a grace countdown, or "held by connection".

## 8. Client lifecycle

`loommux.client.LeaseAwareClient` owns policy discovery, header pinning, MCP
connection lifetime, and the optional heartbeat task:

```python
from loommux.client import LeaseAwareClient


async with LeaseAwareClient(
    "http://127.0.0.1:8801/mcp",
    "agent-a",
    resource_name="shared-analysis",
) as client:
    result = await client.call_tool(
        "run_cell",
        {"freeform": "state = 1\nprint(state)"},
    )
```

The heartbeat task starts only for a heartbeat policy and is cancelled before
the MCP client context closes.

A connection policy needs nothing from this client: the FastMCP Streamable
HTTP transport it wraps keeps the session's standalone `GET` stream open for
the whole context, and that open stream holds the lease.

## 9. Server settings

| Environment variable | Default |
| --- | ---: |
| `LOOMMUX_LEASE_MODE` | `activity` |
| `LOOMMUX_PRIVATE_TTL_SECONDS` | `4800` |
| `LOOMMUX_NAMED_TTL_SECONDS` | `86400` |
| `LOOMMUX_HEARTBEAT_INTERVAL_SECONDS` | `15` |
| `LOOMMUX_HEARTBEAT_TIMEOUT_SECONDS` | `60` |
| `LOOMMUX_CONNECTION_GRACE_SECONDS` | `60` |
| `LOOMMUX_SWEEP_INTERVAL_SECONDS` | `10` |
| `LOOMMUX_ORPHAN_GRACE_SECONDS` | `30` |

`LOOMMUX_LEASE_MODE` accepts `activity`, `heartbeat`, or `connection`.

The lease durations are declared once in `resource/policy.py`; the settings
dataclass and the environment reader both resolve to those same values, so an
unset variable cannot fall back to a duration the project no longer advertises.

All durations must be positive. Heartbeat timeout must be strictly greater
than heartbeat interval.

## 10. Source ownership

```text
src/loommux/resource/
    model.py       resource, lease, address, and lifecycle values
    policy.py      immutable policy generations
    connections.py per-session stream presence reported by the transport
    liveness.py    lease standing derived from renewal and presence facts
    settings.py    server-owned environment defaults
    routing.py     MCP request identity to resource address
    manager.py     provisioning, leases, recovery, sweeping, and retirement

src/loommux/mcp/
    factory.py     eight-tool routing into selected resources
    lease_ping.py  standard MCP ping renewal adapter
    stream_presence.py  standalone GET stream observation for connection leases
    control.py     HTTP operational routes
    console.html   dependency-free management interface

src/loommux/client.py
    policy-pinning Streamable HTTP client and heartbeat owner
```

`IPythonSession` remains protocol-neutral. It owns one logical workbench's
execution history and replaceable `KernelSession`; resource code owns
multiplicity and participation; MCP code owns transport projection.

## 11. Event-loop discipline

An HTTP server runs every tool call, the sweep, the console, the control
plane, and uvicorn's own Ctrl+C and SIGTERM handling on one asyncio event
loop. Anything that holds that loop stops all of them at once, and the
operator can no longer even stop the process gracefully. Three rules keep the
loop free:

1. Work that can take long runs in worker threads: kernel start, restart,
   shutdown, interrupt, and waits on a running cell all go through
   `asyncio.to_thread`. What stays on the loop must be cheap and bounded.
   Kernel liveness, read on the loop by every tool call, sweep, and console
   refresh, is therefore a poll of the kernel process. Jupyter's
   `KernelManager.is_alive` is not: it is a `run_sync` wrapper that, called
   from a thread with a running loop, waits on a helper thread without a
   timeout.
2. A retry loop on the event loop must suspend between attempts, or be
   unable to repeat without one. Awaiting a coroutine that completes without
   suspending does not yield the loop, so a `while True` whose every await
   happens to complete synchronously spins forever.
3. Observers read and never write the state that decides admission. Section 4
   gives the lifecycle rule and the freeze that a stored observation caused.

When a server stops answering, `kill -USR1 <pid>` prints every thread's stack
to stderr without stopping it (not on Windows). Take several dumps a second
or two apart and compare them: a single dump of a spinning loop catches one
arbitrary frame of the spin and can look like a thread blocked somewhere it
only passes through.

These rules were verified under load: ten concurrent private and shared
clients ran random `run_cell`, `wait`, `status`, `interrupt`, and `restart`
calls, cancelled mid-call and disconnected at random, against a server with a
0.2-second sweep and 1-second grace periods while a watchdog polled the
console. Before the fixes it froze within about ten seconds; after them it
served 4,543 calls from 705 sessions over 180 seconds without an error, the
console never took longer than 0.2 seconds to answer, and the kernel
processes matched the registered resources once the load stopped.
