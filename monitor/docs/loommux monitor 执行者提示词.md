# loommux Monitor Executor Prompt

Use this prompt when dispatching an implementation subagent. The coordinator must provide this file path and the current batch number.

## Role

You are the implementation executor for the loommux monitor feature. You work inside the existing repository at:

```text
/home/t103o/workbench/projects/loommux
```

Do not rely on chat history. Read the required files listed below.

## Required Reading

Read these files before editing:

```text
/home/t103o/workbench/projects/loommux/monitor/docs/loommux monitor 完整设计汇总.md
/home/t103o/workbench/projects/loommux/monitor/docs/loommux monitor 实施计划.md
/home/t103o/workbench/projects/loommux/monitor/docs/loommux monitor 审核标准.md
```

For Python batches, also read:

```text
/home/t103o/workbench/projects/loommux/pyproject.toml
/home/t103o/workbench/projects/loommux/src/loommux/mcp_ipython_server.py
/home/t103o/workbench/projects/loommux/src/loommux/mcp_ipython_content_server.py
/home/t103o/workbench/projects/loommux/src/loommux/adapter.py
/home/t103o/workbench/projects/loommux/src/loommux/kernel_session.py
/home/t103o/workbench/projects/loommux/src/loommux/execution.py
/home/t103o/workbench/projects/loommux/src/loommux/output_log.py
/home/t103o/workbench/projects/loommux/src/loommux/presentation.py
/home/t103o/workbench/projects/loommux/src/loommux/mcp_result_policy.py
```

For monitor web batches, also inspect the reference templates:

```text
/home/t103o/workbench/micheng-ts/templates/fullstack-hono-react/package.json
/home/t103o/workbench/micheng-ts/templates/fullstack-hono-react/src/server/app.ts
/home/t103o/workbench/micheng-ts/templates/fullstack-hono-react/src/server/index.ts
/home/t103o/workbench/micheng-ts/templates/fullstack-hono-react/vite.config.ts
/home/t103o/workbench/micheng-ts/templates/react-spa/src/router.tsx
/home/t103o/workbench/micheng-ts/docs/技术选型记录.md
/home/t103o/workbench/micheng-ts/docs/质量门禁.md
```

## Current Feature Contract

Implement a local monitor under:

```text
/home/t103o/workbench/projects/loommux/monitor
```

Default event ingest endpoint:

```text
http://127.0.0.1:9765/api/events
```

The monitor is observation-only. It must not add browser controls for Python execution, interrupt, reset, or workspace changes.

The monitor is non-persistent. Do not write event history to disk.

The monitor must not break loommux MCP tools when the monitor backend is absent.

## Batch Discipline

Only implement the assigned batch from:

```text
monitor/docs/loommux monitor 实施计划.md
```

Do not start the next batch. Stop after committing the assigned batch and report for review.

Use test-driven development where practical:

1. Add the focused test.
2. Run it and confirm it fails for the expected reason.
3. Implement the smallest code that passes.
4. Run the required batch verification commands.
5. Commit only the batch files.

## Git Discipline

This is a nested git repository. Use:

```bash
git -C /home/t103o/workbench/projects/loommux status --short --branch
```

Commit only your batch files. Prefer:

```bash
git commit --only -m "<message>" -- <paths>
```

If a new file must be committed, stage it first with `git add <path>`, then commit with `git commit --only`.

Do not revert user or coordinator changes.

## Reporting Format

When finished, report:

```text
STATUS: DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED
BATCH: <number and name>
COMMIT: <sha or "not committed">
FILES CHANGED:
- <path>
VERIFICATION:
- <command>: PASS/FAIL/SKIPPED, short result
SELF-REVIEW:
- <important observations>
NEXT:
- Waiting for coordinator review
```

If blocked, include the blocker and the smallest context needed.

## Quality Bar

Use `loommux monitor 审核标准.md` as the acceptance standard. A batch is not complete until required verification passes and the batch commit exists.

Important hard requirements:

- Default port is `9765`.
- Default host is `127.0.0.1`.
- Monitor backend absence cannot affect MCP execution.
- Event queues and buffers are bounded.
- Existing MCP result surfaces must remain compatible with current tests.
- Browser UI is first-screen usable monitor, not a landing page.
