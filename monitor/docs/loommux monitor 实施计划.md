# Historical Planning Notice

> This implementation plan predates the integer execution control plane. Current monitor behavior is defined by the [execution control-plane design](../../docs/ipython-mcp-execution-control-plane-design.md) and [monitor README](../README.md); do not use this plan as an interface contract.

# loommux Monitor Implementation Plan

> For agentic workers: read this file together with `loommux monitor 完整设计汇总.md`, `loommux monitor 执行者提示词.md`, and `loommux monitor 审核标准.md` before editing. Implement batch-by-batch. Do not skip verification or commits.

## Goal

Build a local, non-persistent browser monitor for loommux MCP Python tools. The monitor records MCP tool calls and Python execution lifecycle events, forwards them to a localhost web backend on port `9765`, and displays them in a React 19 browser UI.

## Architecture

The Python MCP server remains the source of execution truth. A non-blocking monitor publisher observes tool calls and execution lifecycle events, queues bounded JSON events, and posts them to `http://127.0.0.1:9765/api/events` by default. A Hono backend in `monitor/` receives events, keeps a bounded in-memory ring buffer, and broadcasts events to browser clients through SSE. The React UI aggregates events into an execution-focused view and a tool-call timeline.

## Tech Stack

- Python: existing loommux package, `uv`, pytest, ruff/basedpyright config already present.
- Python HTTP publishing: prefer `httpx`; add it through `uv add httpx` if implementation uses it.
- Monitor web: pnpm, React 19, React DOM 19, Vite 8, TypeScript 6 strict, Tailwind CSS v4, Hono, `@hono/node-server`, Biome, Vitest, Playwright, lucide-react.
- Default host and port: `127.0.0.1:9765`.

## Non-Negotiable Constraints

- Monitor backend absent must never break or materially delay MCP tools.
- Existing MCP tool return surfaces must remain compatible with current tests.
- No disk persistence for events.
- Browser UI is observation-only. Do not add controls to run Python, interrupt, reset, or set workspace.
- Default network exposure is localhost only.
- Do not turn loommux into a TypeScript monorepo. `monitor/` is a focused subproject under the Python repo.

## Batch 0: Orientation And Baseline

### Files To Read

- `pyproject.toml`
- `src/loommux/mcp_ipython_server.py`
- `src/loommux/mcp_ipython_content_server.py`
- `src/loommux/adapter.py`
- `src/loommux/kernel_session.py`
- `src/loommux/execution.py`
- `src/loommux/output_log.py`
- `src/loommux/presentation.py`
- `src/loommux/mcp_result_policy.py`
- `tests/test_ipython_mcp_adapter_blackbox.py`
- `tests/test_output_log.py`
- `monitor/docs/loommux monitor 完整设计汇总.md`
- `monitor/docs/loommux monitor 审核标准.md`

### Required Actions

- Confirm current test baseline before editing:

```bash
uv run pytest
```

- If baseline fails before changes, record the exact failing tests and stop for coordinator review.
- Confirm there are no unrelated staged files:

```bash
git status --short
```

### Commit

No commit is required if no files change. If the worker adds only local notes, remove them before proceeding.

## Batch 1: Python Event Model And Publisher

### Objective

Introduce a small, bounded, non-blocking monitor publishing layer without wiring it into MCP tools yet.

### Files

- Create: `src/loommux/monitoring.py`
- Create: `tests/test_monitoring.py`
- Modify: `pyproject.toml` only if `httpx` is used.

### Required Design

`monitoring.py` must provide:

- `DEFAULT_MONITOR_URL = "http://127.0.0.1:9765/api/events"`
- environment override: `LOOMMUX_MONITOR_URL`
- disable switch: `LOOMMUX_MONITOR_DISABLED=1`
- a typed event shape based on plain dictionaries accepted by existing Python typing style.
- `MonitorPublisher` protocol or class with `publish(event: Mapping[str, Any]) -> None`.
- `NoopMonitorPublisher`.
- a bounded background publisher that enqueues events in the caller path and posts from a daemon worker.
- hard bounds for queued events and text field size.
- short network timeout and failure swallowing.
- `close()` to stop worker during adapter/server shutdown.

The publisher must not log noisy failures to stdout/stderr, because MCP tool output surfaces must stay clean.

### Required Tests

Tests must prove:

- disabled environment returns no-op publisher.
- default URL is `http://127.0.0.1:9765/api/events`.
- `publish()` returns quickly when no backend is available.
- queue overflow drops old events or records dropped count without unbounded growth.
- `close()` is idempotent.

### Verification

```bash
uv run pytest tests/test_monitoring.py -v
uv run pytest tests/test_output_log.py tests/test_presentation.py -v
```

### Commit

```bash
git add pyproject.toml src/loommux/monitoring.py tests/test_monitoring.py
git commit --only -m "Add non-blocking monitor event publisher" -- pyproject.toml src/loommux/monitoring.py tests/test_monitoring.py
```

If `pyproject.toml` was not modified, omit it from `git add` and `git commit --only`.

## Batch 2: Tool Call And Execution Event Hooks

### Objective

Wire monitor events into the MCP tool layer and Python execution lifecycle while preserving current tool behavior.

### Files

- Modify: `src/loommux/mcp_ipython_server.py`
- Modify: `src/loommux/mcp_ipython_content_server.py`
- Modify: `src/loommux/adapter.py`
- Modify: `src/loommux/kernel_session.py`
- Modify: `src/loommux/execution.py` only if strictly necessary.
- Modify/Create tests: `tests/test_ipython_mcp_adapter_blackbox.py`, `tests/test_ipython_mcp_content_server.py`, `tests/test_monitoring.py`, or `tests/test_ipython_mcp_monitor_events.py`.

### Required Design

- Every current MCP tool emits `tool_call_started` and `tool_call_finished` events.
- `tool_call_finished` includes `tool_name`, `call_id`, `duration_ms`, `ok`, `status`, compact `result_summary`, and pretty text summary when available.
- `run_python` emits `execution_submitted` with `execution_id`, `call_id`, `workspace`, `kernel_pid`, `code`, and timeout.
- stdout/stderr/result/traceback increments emit `execution_output` with `execution_id`, `stream`, `text`, and `execution_count`.
- terminal status emits `execution_finished` with `status`, `output_log`, `output_total_lines`, and error summary.
- reset/kill must emit a killed execution event when it kills a running execution.
- Event hooks must be thread-safe because IOPub collection runs in a background thread.
- Existing tool result pretty text and structured content must not change except for deliberate tests that prove monitor metadata is not leaked.

### Required Tests

Tests must prove:

- A fake publisher receives tool started/finished events for `python_status`.
- A fake publisher receives `execution_submitted`, output, and finished events for `run_python`.
- stdout/stderr/result/traceback map to distinct streams.
- backend absence/no-op publisher does not affect `run_python`.
- current blackbox tests still pass.

### Verification

```bash
uv run pytest tests/test_monitoring.py tests/test_ipython_mcp_adapter_blackbox.py tests/test_ipython_mcp_content_server.py -v
uv run pytest
```

### Commit

```bash
git add src/loommux tests pyproject.toml
git commit --only -m "Emit monitor events for MCP Python tools" -- src/loommux tests pyproject.toml
```

Omit `pyproject.toml` if unchanged.

## Batch 3: Monitor Backend

### Objective

Create the `monitor/` Node subproject backend with event ingest, health, in-memory ring buffer, and SSE stream.

### Files

- Create: `monitor/package.json`
- Create: `monitor/pnpm-lock.yaml` if generated by `pnpm install`.
- Create: `monitor/tsconfig.json`
- Create: `monitor/vite.config.ts`
- Create: `monitor/vitest.config.ts`
- Create: `monitor/tsdown.server.config.ts`
- Create: `monitor/src/shared/schema.ts`
- Create: `monitor/src/shared/types.ts`
- Create: `monitor/src/server/events.ts`
- Create: `monitor/src/server/app.ts`
- Create: `monitor/src/server/index.ts`
- Create: `monitor/src/server/app.test.ts`

### Required API

- `GET /api/health`
- `POST /api/events`
- `GET /api/events/stream`

`POST /api/events` must validate event shape, assign sequence numbers, buffer bounded events, and broadcast to connected SSE clients.

`GET /api/events/stream` must send an initial snapshot and heartbeat. It must close cleanly when the client disconnects.

`GET /api/health` must report `ok`, `started_at`, `uptime_ms`, `clients`, `events_buffered`, `events_received`, and `events_dropped`.

### Required Tests

Vitest tests must prove:

- health returns expected fields.
- legal event POST returns sequence and increments received count.
- illegal event POST returns 400.
- ring buffer drops old events at configured capacity.
- SSE stream receives a posted event.

### Verification

```bash
cd monitor
pnpm install
pnpm typecheck
pnpm test
pnpm build
```

### Commit

```bash
git add monitor/package.json monitor/pnpm-lock.yaml monitor/tsconfig.json monitor/vite.config.ts monitor/vitest.config.ts monitor/tsdown.server.config.ts monitor/src
git commit --only -m "Add loommux monitor backend" -- monitor/package.json monitor/pnpm-lock.yaml monitor/tsconfig.json monitor/vite.config.ts monitor/vitest.config.ts monitor/tsdown.server.config.ts monitor/src
```

## Batch 4: React Monitor UI

### Objective

Build the observation-only browser UI that connects to SSE and renders execution and tool-call views.

### Files

- Create: `monitor/index.html`
- Create: `monitor/src/client/main.tsx`
- Create: `monitor/src/client/App.tsx`
- Create: `monitor/src/client/state.ts`
- Create: `monitor/src/client/events.ts`
- Create: `monitor/src/client/styles.css`
- Create: `monitor/src/client/components/*` as needed.
- Create: `monitor/src/client/App.test.tsx`

### Required UI

- First screen is the monitor, not a landing page.
- Status bar: SSE connection state, backend health, event count, dropped count, latest event time, clear view, auto-scroll/pause controls.
- Execution list: execution id, status, time, duration, code first line, output line count, error summary.
- Execution detail: code, combined output, stdout/stderr/result/traceback tabs, output_log, status, error.
- Tool timeline: tool name, arguments summary, result summary, duration, status.
- Copy buttons for code and output.
- Clear view only clears browser/monitor view; it must not imply deletion of MCP execution history.
- No run/interrupt/reset/workspace controls.

### Required Tests

React tests must prove:

- submitted/output/finished events render code, output, and status.
- error/traceback events render error and traceback.
- tool events render timeline rows and result summary.
- clear view clears displayed events.

### Verification

```bash
cd monitor
pnpm typecheck
pnpm test
pnpm build
```

### Commit

```bash
git add monitor/index.html monitor/src/client monitor/src/shared monitor/package.json monitor/pnpm-lock.yaml
git commit --only -m "Add React monitor UI" -- monitor/index.html monitor/src/client monitor/src/shared monitor/package.json monitor/pnpm-lock.yaml
```

## Batch 5: End-To-End Integration And Documentation

### Objective

Prove the Python MCP publisher, Hono backend, and React UI work together. Document commands and final quality gates.

### Files

- Create: `monitor/README.md`
- Create: `monitor/playwright.config.ts`
- Create: `monitor/e2e/monitor.spec.ts`
- Modify: `monitor/package.json`
- Modify: `.gitignore` if monitor build/test outputs require ignores.
- Modify: `monitor/docs/loommux monitor 完整设计汇总.md` only for factual corrections, not to rewrite the design.

### Required Documentation

`monitor/README.md` must include:

- Purpose and scope.
- Start backend/frontend commands.
- Default port `9765`.
- Environment variables: `LOOMMUX_MONITOR_URL`, `LOOMMUX_MONITOR_DISABLED`.
- Security warning: localhost only by default and events may include code/output.
- Non-persistence statement.
- Verification commands.

### Required E2E

Playwright must validate:

- monitor page loads.
- SSE status area is visible.
- posting a sample `execution_submitted` and `execution_output` event to backend makes code/output appear in browser.
- desktop and mobile viewports do not show obvious empty/overlapped primary UI.

### Verification

Run from repo root:

```bash
uv run pytest
cd monitor
pnpm typecheck
pnpm test
pnpm build
pnpm e2e
```

If `pnpm e2e` cannot run because browsers or server startup fail, document the exact blocker and still run `pnpm build` and unit tests.

### Commit

```bash
git add .gitignore monitor
git commit --only -m "Document and verify loommux monitor integration" -- .gitignore monitor
```

Omit `.gitignore` if unchanged.

## Final Acceptance Criteria

- `uv run pytest` passes.
- `cd monitor && pnpm typecheck && pnpm test && pnpm build` passes.
- `cd monitor && pnpm e2e` passes or has a documented environment blocker.
- Existing MCP tool schemas and pretty text tests remain green.
- Monitor backend absent does not break Python MCP tools.
- Monitor backend present receives events on port `9765`.
- Browser displays tool timeline, execution list, code, output streams, and terminal status.
- No event persistence exists beyond bounded memory.
- Browser UI includes no Python control actions.
- Each batch is committed with only related files.

## Coordinator Review Gates

After every batch, stop for coordinator review. The coordinator must perform:

- Spec compliance review against `loommux monitor 完整设计汇总.md`.
- Code quality review against `loommux monitor 审核标准.md`.
- Verification review using the commands listed in the batch.

Do not start the next batch until the coordinator explicitly approves the current batch.
