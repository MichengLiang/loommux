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
provisioning
running
orphaned
closing
stopped
crashed
```

`busy` is not a lifecycle state. It is derived from active control operations
or a running IPython execution.

Provisioning is deduplicated per resource address. Concurrent callers for one
address await the same startup task; unrelated addresses may start kernels in
parallel. Slow kernel readiness therefore does not hold the registry lock or
serialize every resource.

`restart` preserves the logical resource, lease set, execution records, and
next execution number while replacing the kernel process and namespace.
`recycle` removes the logical resource and closes its session.

If a kernel exits unexpectedly, its running execution is finished as `killed`
with a `KernelProcessExited` diagnostic. The resource becomes recoverable and
the next operation restarts the `IPythonSession` under the same resource ID.

Jupyter's local provisioner starts Unix kernels in a new session and signals
their process group during termination. Windows uses the loommux Job Object
containment. Tests require restart to terminate descendants on both supported
platform families.

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
```

The current defaults may change, but an existing lease continues to use the
generation selected when it was created. Repeating an unchanged policy update
is idempotent and does not allocate a generation.

A lease-aware client discovers `/api/lease-policy` before MCP initialization
and sends the selected generation in
`X-Loommux-Lease-Policy-Generation`. This closes the race between policy
discovery and first resource use.

### Activity mode

Tool entry and completion refresh the lease deadline. Private and named
resources use their corresponding fixed activity timeout.

### Heartbeat mode

Tool activity and successful standard MCP `ping` refresh the heartbeat
deadline. The client sends ping at the advertised interval. The timeout must
be greater than the interval.

Ping never creates a resource or missing lease. Kernel health observation is
also separate from client liveness and does not renew participation.

## 6. Expiry and orphaned execution

A sweep first removes expired leases whose client has no active operation.
It then evaluates resources with no remaining participants.

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
| `POST` | `/api/resources/{id}/recycle` | Recycle one resource; `force` permits busy retirement. |
| `POST` | `/api/resources/recycle-idle` | Recycle resources without active work. |
| `POST` | `/api/resources/recycle-all` | Force retirement of every resource. |

The control plane is operational infrastructure. It is not added to MCP
`tools/list`.

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

## 9. Server settings

| Environment variable | Default |
| --- | ---: |
| `LOOMMUX_LEASE_MODE` | `activity` |
| `LOOMMUX_PRIVATE_TTL_SECONDS` | `1800` |
| `LOOMMUX_NAMED_TTL_SECONDS` | `86400` |
| `LOOMMUX_HEARTBEAT_INTERVAL_SECONDS` | `15` |
| `LOOMMUX_HEARTBEAT_TIMEOUT_SECONDS` | `60` |
| `LOOMMUX_SWEEP_INTERVAL_SECONDS` | `10` |
| `LOOMMUX_ORPHAN_GRACE_SECONDS` | `30` |

All durations must be positive. Heartbeat timeout must be strictly greater
than heartbeat interval.

## 10. Source ownership

```text
src/loommux/resource/
    model.py       resource, lease, address, and lifecycle values
    policy.py      immutable policy generations
    settings.py    server-owned environment defaults
    routing.py     MCP request identity to resource address
    manager.py     provisioning, leases, recovery, sweeping, and retirement

src/loommux/mcp/
    factory.py     eight-tool routing into selected resources
    lease_ping.py  standard MCP ping renewal adapter
    control.py     HTTP operational routes
    console.html   dependency-free management interface

src/loommux/client.py
    policy-pinning Streamable HTTP client and heartbeat owner
```

`IPythonSession` remains protocol-neutral. It owns one logical workbench's
execution history and replaceable `KernelSession`; resource code owns
multiplicity and participation; MCP code owns transport projection.
