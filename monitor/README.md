# loommux Monitor

The loommux monitor is an observation-only local web UI for MCP tool calls and Python execution lifecycle events. The Python MCP server remains the execution authority; the monitor receives events, keeps a bounded in-memory backend buffer, and renders recent activity in the browser.

## Scope

- Shows MCP tool-call timelines and Python execution details.
- Receives events through `POST /api/events` and streams them to the browser through SSE.
- Does not run Python, interrupt executions, reset kernels, or set workspaces.
- Does not persist event history to disk.

## Start Commands

Install dependencies from this directory:

```bash
pnpm install
```

Start the monitor backend:

```bash
pnpm dev
```

The backend defaults to `127.0.0.1:9765`.

For frontend development, run Vite in a second terminal:

```bash
pnpm exec vite --host 127.0.0.1 --port 5175
```

Production build:

```bash
pnpm build
```

## Python Publisher Configuration

The Python publisher posts to:

```text
http://127.0.0.1:9765/api/events
```

Environment variables:

- `LOOMMUX_MONITOR_URL`: override the ingest endpoint.
- `LOOMMUX_MONITOR_DISABLED=1`: disable monitor publishing.

## Security And Persistence

The backend is localhost-only by default. Monitor events may include Python code, stdout, stderr, results, tracebacks, tool arguments, and result summaries. Do not expose the server on a public interface without treating it as sensitive execution telemetry.

Event storage is memory-only. Backend restarts, browser refreshes, and bounded-buffer overflow can lose visible history. The clear-view control only clears the browser view; it does not delete MCP execution history.

## Verification

From the loommux repo root:

```bash
uv run pytest
```

From `monitor/`:

```bash
pnpm typecheck
pnpm test
pnpm build
pnpm e2e
```
