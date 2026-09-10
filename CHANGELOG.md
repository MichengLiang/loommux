# Changelog

All notable changes to loommux are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

No unreleased changes.

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

[Unreleased]: https://github.com/MichengLiang/loommux/compare/v0.1.14...HEAD
[0.1.14]: https://github.com/MichengLiang/loommux/compare/v0.1.13...v0.1.14
[0.1.13]: https://github.com/MichengLiang/loommux/compare/v0.1.12...v0.1.13
[0.1.12]: https://github.com/MichengLiang/loommux/compare/v0.1.11...v0.1.12
[0.1.11]: https://github.com/MichengLiang/loommux/compare/v0.1.10...v0.1.11
[0.1.10]: https://github.com/MichengLiang/loommux/compare/v0.1.9...v0.1.10
