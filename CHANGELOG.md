# Changelog

All notable changes to loommux are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Breaking:** a kernel opened by name from a kernel is a child kernel of
  that kernel's main kernel, its home, instead of a globally named kernel held
  by every kernel that opened it. Names resolve inside the asking kernel's
  home, so two sessions that both open `build` get two kernels, and a child
  kernel opening a kernel opens it in the same home. A child kernel lives
  exactly as long as its main kernel holds a client lease.
- **Breaking:** inside a kernel, `loommux.kernels()` lists its home only;
  outside a kernel, `Daemon()` lists every kernel and resolves a main kernel by
  name or any kernel by id. `GET /api/resources?home=<id>` lists one home.
- **Breaking:** resource snapshots report `scope` (`main` or `child`), `home`,
  and `named` instead of `key`, `shared`, and `held_by`; the console shows the
  home, and the `Kernels` table its scope and home columns.
- A session works in the main kernel that holds its lease, so the lease is
  its binding. A generated `private-<session>` name that another session's
  kernel already has is numbered instead of shared.

### Removed

- **Breaking:** `ResourceAddress`, `resolve_address`, and `named_address`
  from `loommux.resource`; `KernelRequest` and `connection_request` describe
  what a call asks for, and `LeasePolicy.timeout_for` takes `named=`.

## [0.3.0] - 2026-10-22

### Changed

- **Breaking:** the cell header grammar moved from `loommux.submission.header`
  to `loommux.cell_header`; the `loommux.submission` package is gone.

### Removed

- **Breaking:** the daemon no longer converts a triple-quoted Python literal
  holding an Apply Patch program into a plain `str` before submission. Every
  cell reaches the kernel exactly as written; pass patch text through a cell
  magic, whose body IPython delivers verbatim. The conversion did not know the
  body's language, so matching text inside a `%%bash` heredoc or a forwarded
  cell was rewritten as well.

## [0.2.0] - 2026-10-21

### Added

- The `connection` lease mode holds a lease for as long as its MCP session
  keeps the standalone Streamable HTTP `GET` stream open, so a client such as
  Claude Code keeps its kernel while it runs, however long it idles, and
  releases it about a minute after it exits, without pinging. Select it with
  `LOOMMUX_LEASE_MODE=connection`. Stdio and in-process sessions are connected
  for the life of the server process.
- Every tool result of a connection-mode session whose client never opens a
  stream (`unbound`) or keeps calling after its stream is gone
  (`stream_lost`) ends with a `Lease alert` block, the structured status
  carries `lease_alert`, and the server logs one `lease alert` warning when a
  lease enters such a standing. The lease falls back to the activity window
  instead of being released.
- `/api/resources` reports each lease's `standing`, `alert`, and
  `grace_remaining_seconds`; `remaining_seconds` and `timeout_seconds` are
  `null` while an open stream holds a connection lease.
- `/api/lease-policy`, its `PUT`, `LeaseAwareClient`, and
  `LOOMMUX_CONNECTION_GRACE_SECONDS` (default 60) carry the connection grace
  period.
- The resource console offers the `connection` mode and its grace, shows each
  lease's standing and grace countdown, and keeps a lease-signal row that
  turns red while any lease is in an alert standing.
- A cell may begin with a `%%loommux [--kernel NAME] [--wait SECONDS] [--full-output]`
  header, written in ordinary IPython cell-magic syntax. Its options control
  that one cell; another cell magic such as `%%bash` may follow it, top-level
  `await` keeps working, and IPython history records the header as written.
  A cell has one header: a second `%%loommux` line right after a header for
  this kernel is reported as a `UsageError`, so every option is read from the
  first line by both the daemon and the kernel.
- `%%loommux --kernel build` runs the rest of the cell in the kernel named
  `build`, opened on first use. Its output is relayed into the cell as it
  arrives, an error there ends the cell with a one-line `ForwardedCellError`
  below the relayed traceback, an interrupt is passed on, and a forward back
  into a busy kernel fails at once with `busy`.
- Every kernel knows its resource (`LOOMMUX_KERNEL_ID`, `LOOMMUX_KERNEL_NAME`,
  and for an HTTP server `LOOMMUX_CONTROL_URL`) and holds `loommux`, a client
  of its daemon: `loommux.kernels()`, `loommux.self`, `loommux["lab"]` with
  `run()`, `restart()`, `interrupt()`, and `recycle()`, and jobs with
  `wait()`, `status()`, `output()`, and `follow()`. `loommux.daemon.Daemon()`
  is the same client outside a kernel (default `http://127.0.0.1:8801`), and
  `close()` releases its connections.
- A kernel opened by name is held by the kernel that opened it and lives as
  long as a client lease reaches it through its holders; kernels that only hold
  each other are released. Snapshots and `loommux.kernels()` report `held_by`,
  and the console shows it.
- Control routes for programs: `GET /api/resources/{id}`,
  `POST /api/resources/ensure`, and `/api/resources/{id}/executions` with
  status, `wait`, `output`, and `combined` (follow by character offset).
- Help that IPython sends to a pager (`obj?`, `obj??`, `%%loommux?`) is part of
  the output, after the cell's other output, instead of being lost.
- On Linux and macOS, `kill -USR1 <pid>` prints every thread's Python stack to
  a loommux server's stderr without stopping it, including while its event
  loop is frozen.
- `scripts/stress.py` loads a private server with random, cancelled, and
  disconnected tool calls and a cell that forwards to a shared named kernel,
  then requires every kernel process and file descriptor to be released;
  CONTRIBUTING requires it for lifecycle, concurrency, and output changes and
  for releases.

### Changed

- **Breaking:** the `# loommux:` control comment is gone; it is an ordinary
  Python comment now. Write `%%loommux --wait 120 --full-output` as the first
  line instead. An invalid header returns `invalid_cell_header`, which replaces
  `invalid_loommux_directive`.
- **Breaking:** a resource no longer has a `crashed` lifecycle. A registered
  resource is `running` or `orphaned`. `/api/resources` reports the kernel
  separately as `kernel_state` (`running`, `restarting`, or `stopped`) with a
  `null` `kernel_pid` while no kernel runs; the console shows "restarting" or
  "not running", and the `status` tool shows `kernel: restarting` during a
  restart.
- **Breaking:** `LeasePolicy` requires `connection_grace_seconds`, and
  `LeaseAwareClient` requires a server whose `/api/lease-policy` publishes it;
  pair client and server from the same release.
- **Breaking:** the server factory given to `run_entrypoint` receives the
  control URL as a second argument (`None` for stdio).
- `POST /api/resources/{id}/recycle` answers 404 for a missing resource, as
  every other resource route does (it answered 409), and rejects a malformed
  JSON body with 400 instead of reading it as `{}`. An empty body still means
  no options.
- The sweeper releases resources that no client lease reaches, directly or
  through holders, instead of resources without a lease of their own.
- Kernels on Linux and macOS use ZMQ IPC sockets in their private runtime
  directory instead of loopback TCP ports, so no other process can take a
  kernel's port during startup, and kernels no longer log a "running over TCP
  without encryption" warning each. Windows kernels keep TCP.
- `import loommux` no longer imports the session, so loading the kernel
  extension costs each kernel about 4 ms instead of about 85 ms;
  `loommux.IPythonSession` and `loommux.__version__` resolve on first use.
- loommux declares `ipython>=9.17.1` directly: the header is located with
  IPython's own cleanup transforms.

### Fixed

- Fix a server freeze. A sweep, console refresh, or health check that read a
  resource while a restart had no kernel yet stored it as `crashed`; nothing
  reset that after the restart succeeded, and the next tool call on the
  resource retried without ever yielding the event loop, so the console, new
  connections, Ctrl+C, and SIGTERM all stopped responding.
- Serialize the `restart` and `interrupt` tools with kernel recovery. A tool
  call that arrived during a restart and found no kernel started a second
  kernel, and whichever of the two was installed first was overwritten and
  never shut down.
- A tool call that was waiting to recover a dead kernel while its resource
  was recycled continues on the address's new resource instead of failing
  with "resource was retired during recovery", as it already did when the
  recycle landed a moment later.
- Require `mcp>=1.30.0`. Older SDKs kept every MCP session the HTTP server
  ever opened: a session closed with `DELETE` was never deregistered and one
  whose client left without it was never reaped, so a long-running server
  grew with every client it had served (809 sessions retained after a
  two-minute load). 1.30.0 deregisters on `DELETE` and reaps sessions idle
  for 30 minutes, an open `GET` stream not counting as idle.
- Check kernel liveness by polling the kernel process instead of calling
  Jupyter's `KernelManager.is_alive`. That call waited on a helper thread
  without a timeout whenever the server's event loop asked (every tool call,
  sweep, and console refresh), and every idle kernel's iopub collector asked
  ten times a second.
- Close the event loops that Jupyter's blocking calls create for a kernel
  when the kernel shuts down. Each start, restart, and shutdown left loops
  that only a full garbage collection freed, which an idle server never ran:
  a server holding one kernel kept 863 file descriptors after a minute of
  load.
- Retry a failed kernel start once with a fresh process on restart and on the
  Windows interrupt replacement, as the first start already did. A restart
  that lost a port to another process failed outright.
- Record cell output in time linear in its size. Each output chunk used to
  rebuild the execution's whole retained text three times over, so a cell
  printing 10 MB in 1 KB chunks spent 44 s in loommux (now 1.5 s), and
  progress bars slowed down the longer a cell ran.
- Raise the console's muted text and green badge colors to WCAG AA contrast
  (4.35:1 and 4.43:1 before), and its policy labels from 11 px to 14 px.
- Stop demoting the console's small text twice: metric labels go from 10 px
  gray to 12 px in the ink color, and resource IDs from 11 px to 12 px.
- Document that a standard MCP `ping` renews a lease under every mode, not
  only in heartbeat mode.
- The manual FastMCP lease example accepts a `connection` policy instead of
  rejecting it as an unknown mode.

## [0.1.15] - 2026-09-10

### Fixed

- Declare the MCP SDK range that loommux is written against
  (`mcp>=1.29.1,<2`). Loommux imports `mcp.types` directly and installs its
  lease-renewal handler on the SDK's low-level ping request registry, but no
  requirement stated any of that: the SDK arrived only through FastMCP's
  `server` extra, so `loommux` depended on internals of a package whose version
  it never named.
- Document why the heartbeat hook lives on the SDK handler registry and which
  majors invalidate it, so a future dependency bump cannot move `ping` into
  FastMCP's middleware and leave leases silently unrenewed.

## [0.1.14] - 2026-09-10

### Added

- Give `LeaseAwareClient` the complete FastMCP client surface: `list_tools()`
  for discovery plus a `call_tool()` that forwards `version`, `timeout`,
  `progress_handler`, `raise_on_error`, `meta`, `task`, `task_id`, and `ttl`,
  with overloaded result types that distinguish `CallToolResult` from
  `ToolTask`. Lease awareness decorates the connection lifecycle instead of
  narrowing the ordinary client contract, so a host that registers tools
  dynamically can call them without reaching through to the private FastMCP
  client that owns the transport.

### Changed

- Make the operator label optional. Omitting it or passing `None` leaves the
  `X-Loommux-Operator` header absent, so the server keeps its session-derived
  fallback display name instead of receiving a blank operator identity.
- Extend the default private activity timeout from 30 to 80 minutes, so an MCP
  host that pauses between calls does not lose its private IPython session.
- Have the omitted-output notice name its recovery paths and the
  `# loommux: --full-output` directive, so a caller whose body was omitted sees
  how to read, search, or request the retained output without consulting
  separate documentation. The notice is now several lines; the human-readable
  size line is unchanged.

### Fixed

- Make the declared lease defaults effective. The 80-minute private activity
  timeout lived only on the settings dataclass while
  `ResourceServerSettings.from_environ()` still fell back to 30 minutes, so a
  server started without `LOOMMUX_PRIVATE_TTL_SECONDS` kept the shorter lease.
- Declare every generation-one lease duration once in `resource/policy.py` and
  resolve the settings dataclass, the environment reader, and a directly built
  policy manager against it, so the documented default, the effective default,
  and the programmatic default cannot drift apart again.
- Assert omission semantics instead of copies of the notice prose in the
  presentation tests, so the notice's guidance can change without breaking
  assertions that were only restating the previous wording.

## [0.1.13] - 2026-09-05

### Changed

- Make automatic terminal output delivery use one hard limit of 5,000
  `o200k_base` tokens instead of a line-count policy.
- Keep complete output available through `read_output` and `search_output`
  while omitting oversized automatic `run_cell` and `wait` bodies.
- Align the README, contributor guide, and pull request checklist with the
  supported Python 3.13+ baseline and the maintained example checks.

### Removed

- Remove the obsolete line-count delivery limit, its response field, omission
  reason, formatter, tests, and documentation.
- Remove the tokenizer fallback that substituted an unrelated delivery policy
  when token counting failed.
- Removed obsolete Pueue engine, Rust MSRV, and deleted AsciiDoc audit jobs from
  GitHub Actions.

### Fixed

- Tokenizer failures now propagate instead of silently changing the output
  delivery policy.
- Make the HTTP resource integration test terminate its temporary server with
  Windows-compatible process APIs.
- Keep the external Streamable HTTP subprocess integration test on Linux, where
  its process-lifecycle harness is supported, while Windows continues to run
  the in-process MCP and platform-specific kernel tests.
- Make the shared-lease state-machine test deterministic instead of relying on
  sub-30-millisecond sleeps that vary across hosted operating systems.

## [0.1.12] - 2026-09-05

### Added

- Added session-private and named shared logical kernel resources with
  independent execution histories and client leases.
- Added activity and standard MCP-ping lease policies, policy generations,
  orphaned-execution reclamation, and lazy recovery after kernel crashes.
- Added a lease-aware Streamable HTTP client, HTTP resource APIs, and a
  dependency-free management console.
- Added a runnable `examples/lease-aware-client` guide covering automatic and
  manual FastMCP client cooperation for heartbeat leases.

### Changed

- The eight MCP tools now route to the resource selected by the current MCP
  session instead of sharing one process-global IPython session.
- Kernel restart preserves logical resource identity while resource recycle
  retires the complete session and its retained history.
- Refined the public documentation to keep implementation details behind the
  IPython-oriented MCP operation model.

### Fixed

- Constrained the FastMCP dependency to the validated 3.x API range so release
  builds do not silently resolve an incompatible future major version.

### Removed

- Removed the fully deprecated Pueue engine documentation suite, generated
  diagrams, documentation audit script, and README instructions. Loommux now
  presents only its supported persistent IPython resource model.

## [0.1.11] - 2026-07-24

### Fixed

- Skip the POSIX-only `%%bash` cell-magic test on Windows, where IPython cannot
  provide a Bash shell.

## [0.1.10] - 2026-07-24

### Removed

- Removed the optional local browser monitor, including its React and Hono
  application, event publisher, background delivery thread, environment
  configuration, and monitor-specific tests.
- Removed monitor-specific CI checks and documentation.

### Changed

- MCP tool calls and execution lifecycle handling no longer create monitoring
  side effects such as local HTTP delivery attempts or background event queues.
- PyPI releases now validate that a versioned tag matches the package version,
  run the complete Python quality gate, and validate distribution metadata
  before upload.
- Declared Pillow as a development dependency so rich presentation tests run
  in a clean CI environment.

[Unreleased]: https://github.com/MichengLiang/loommux/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/MichengLiang/loommux/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/MichengLiang/loommux/compare/v0.1.15...v0.2.0
[0.1.15]: https://github.com/MichengLiang/loommux/compare/v0.1.14...v0.1.15
[0.1.14]: https://github.com/MichengLiang/loommux/compare/v0.1.13...v0.1.14
[0.1.13]: https://github.com/MichengLiang/loommux/compare/v0.1.12...v0.1.13
[0.1.12]: https://github.com/MichengLiang/loommux/compare/v0.1.11...v0.1.12
[0.1.11]: https://github.com/MichengLiang/loommux/compare/v0.1.10...v0.1.11
[0.1.10]: https://github.com/MichengLiang/loommux/compare/v0.1.9...v0.1.10
