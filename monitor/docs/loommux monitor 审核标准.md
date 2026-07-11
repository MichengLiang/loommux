# Historical Review Notice

> This review guide predates the integer execution control plane. Validate monitor events against the [execution control-plane design](../../docs/ipython-mcp-execution-control-plane-design.md) and [monitor README](../README.md).

# loommux Monitor Review Standard

This document defines what counts as good, acceptable, and complete for the loommux monitor implementation. Reviewers must use it after every implementation batch.

## Review Order

Review in this order:

1. Spec compliance: does the change implement the assigned batch and nothing outside scope?
2. Behavioral safety: does it preserve existing loommux MCP behavior?
3. Engineering quality: are boundaries, types, tests, and resource limits sound?
4. Verification evidence: did the worker run the required commands and report exact results?

Do not perform code quality approval before spec compliance approval.

## Definition Of Excellent

Excellent work has these properties:

- The implementation changes the smallest needed surface and keeps loommux execution semantics intact.
- Event concepts stay separated: tool-call events are not confused with execution events.
- Monitor publishing is non-blocking on the MCP tool path.
- Every queue, buffer, and text-carrying event has an explicit bound.
- Browser UI presents the first screen as the usable monitor, with no landing page or explanatory filler.
- UI layout is dense, legible, responsive, and stable under long code/output.
- Tests cover both success and failure behavior.
- Verification commands are run fresh after the batch.
- The commit contains only files for the batch.
- Comments explain constraints that are not obvious from data/control flow, especially why publishing must not block MCP.

## Definition Of Done

A batch is done only when:

- All required files for that batch exist or were intentionally avoided with a documented reason.
- Required tests for the batch exist and pass.
- Previously existing relevant tests still pass.
- No unrelated files are modified.
- The worker made a batch commit using `git commit --only`.
- The worker reports exact verification commands and outcomes.
- The coordinator has approved spec compliance and code quality.

The whole feature is done only when:

- All batches in `loommux monitor 实施计划.md` are complete.
- `uv run pytest` passes from `projects/loommux`.
- `cd monitor && pnpm typecheck && pnpm test && pnpm build` passes.
- `cd monitor && pnpm e2e` passes or has a precise environment blocker.
- Browser UI has been checked with Playwright or screenshots in desktop and mobile viewports.
- Monitor backend absent does not affect MCP execution.
- Monitor backend present receives Python MCP events on port `9765`.
- No persistent event storage has been added.

## Hard Failures

Reject the batch if any of these occur:

- MCP tools fail when monitor backend is absent.
- Existing MCP tool schemas or pretty text surfaces change without explicit design approval.
- Monitor event publishing performs slow synchronous network I/O on the tool return path.
- Event buffers can grow without bound.
- The UI adds controls to run Python, interrupt, reset, or set workspace.
- The backend listens on `0.0.0.0` by default.
- The implementation writes event history to disk.
- The batch includes unrelated refactors.
- Tests are skipped without a concrete blocker.
- The worker proceeds to the next batch before review.

## Quantitative Targets

- Default ingest URL: `http://127.0.0.1:9765/api/events`.
- Default server host: `127.0.0.1`.
- Publisher timeout: must be short enough that one failed publish cannot be noticed as a tool delay. Target maximum per background HTTP attempt: `<= 500 ms`.
- Tool path publish overhead: enqueue-only, target `<= 5 ms` for ordinary events.
- Python publisher queue: bounded. Target maximum event count: `1000` unless the implementation documents a better bound.
- Backend ring buffer: bounded. Target maximum event count: `1000` to `5000`.
- SSE heartbeat: present. Target interval: `10` to `30` seconds.
- Frontend long text containers: bounded with internal scrolling. No full-page horizontal overflow at 390 px mobile width.

These are targets, not hidden requirements. If the implementation chooses different numbers, the worker must justify them in code comments or documentation.

## Spec Compliance Checklist

For every review, answer these questions:

- Does the batch match the assigned objective?
- Does the change preserve the observation-only boundary?
- Are all current Python MCP tools represented in tool-call events once Batch 2 is complete?
- Does `run_python` expose code and execution lifecycle events once Batch 2 is complete?
- Does the monitor use port `9765` by default?
- Does the monitor remain localhost-only by default?
- Is event storage memory-only?
- Are failure semantics clear when monitor backend is absent?
- Does the UI show execution and tool-call views after Batch 4?
- Does the README document start commands and environment variables after Batch 5?

## Code Quality Checklist

Review Python code for:

- Thread safety around publisher queues and callbacks.
- No stdout/stderr noise from monitor internals.
- Explicit close/shutdown path.
- No direct HTTP code inside `Execution`.
- Minimal changes to `adapter.py` and `kernel_session.py`.
- Stable event field names.
- Type annotations consistent with existing standard-mode basedpyright setup.

Review TypeScript backend code for:

- Runtime validation of event payloads.
- Correct SSE headers.
- Clean client disconnect handling.
- Ring buffer bound and dropped count.
- No accidental global mutable state outside the app/event store boundary unless it is intentionally process-local.
- Tests that exercise the Hono app without requiring a real browser.

Review React code for:

- Event aggregation logic separated from rendering enough to test.
- No text overflow in buttons, tabs, status badges, or list items.
- Stable dimensions for code/output/detail panels.
- Accessible buttons with labels or tooltips.
- No decorative hero, marketing text, or large ornamental sections.
- No single-hue decorative palette.

## Verification Evidence Checklist

The worker report must include:

- Git commit SHA for the batch.
- Files changed.
- Commands run.
- Pass/fail result for each command.
- Any skipped command and concrete reason.
- Any concern that remains after self-review.

## Coordinator Promotion Rule

Promote the next batch only if:

- Spec compliance review has no open issues.
- Code quality review has no important open issues.
- Required verification passed or the blocker is external and documented.
- The worker committed only the batch work.

If a reviewer finds issues, send the issue list back to the same executor for repair and re-review. Do not patch around the executor unless the coordinator explicitly takes over after a blocker.
