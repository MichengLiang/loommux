# Historical Coordination Notice

> This status snapshot predates the implemented integer execution control plane and is retained only as historical coordination context. Current monitor behavior is documented in [monitor README](../README.md).

# loommux Monitor Coordination State

## Current State

- Feature: loommux MCP Python tool live browser monitor.
- Design status: user approved the design direction and asked for documentation, execution planning, and subagent handoff.
- Default monitor port: `9765`.
- Default monitor host: `127.0.0.1`.
- Current implementation status: documentation and execution package preparation.
- Current required next action: dispatch executor for Batch 0 and Batch 1 only after documentation is committed.

## Canonical Documents

Read these in order:

1. `monitor/docs/loommux monitor 完整设计汇总.md`
2. `monitor/docs/loommux monitor 实施计划.md`
3. `monitor/docs/loommux monitor 审核标准.md`
4. `monitor/docs/loommux monitor 执行者提示词.md`

## Coordination Protocol

- Principal coordinator owns design, review, and promotion between batches.
- Executor owns one assigned implementation batch at a time.
- Executor must stop after the assigned batch and wait for coordinator review.
- Coordinator performs spec compliance review first, then code quality review.
- Coordinator approves the next batch only after open review issues are fixed.
- Do not dispatch multiple implementation batches in parallel because Python hooks and monitor files will overlap.

## Planned Batch Flow

1. Batch 0: Orientation and baseline.
2. Batch 1: Python event model and publisher.
3. Batch 2: Tool call and execution event hooks.
4. Batch 3: Monitor backend.
5. Batch 4: React monitor UI.
6. Batch 5: End-to-end integration and documentation.

## First Executor Assignment

The first subagent should execute Batch 0 and Batch 1 from `loommux monitor 实施计划.md`.

Reason: Batch 0 has no code changes unless baseline fails, and Batch 1 creates the isolated publisher foundation needed before event hooks. Combining them is acceptable because Batch 0 is only orientation/baseline and Batch 1 has a focused write set.

The first subagent must not implement Batch 2.

## Review Requirements After First Executor

Coordinator must check:

- `src/loommux/monitoring.py` exists and has bounded, non-blocking publishing.
- `tests/test_monitoring.py` covers disabled/default/nonblocking/overflow/close behavior.
- Default endpoint is `http://127.0.0.1:9765/api/events`.
- No MCP entry or execution hook changes were made in Batch 1.
- `uv run pytest tests/test_monitoring.py -v` passed.
- Existing focused tests passed.
- Commit contains only Batch 1 files.

## Known User Decisions

- User accepts the proposed design and UX details.
- User wants all current Python MCP tool calls visible in the audit timeline.
- User wants execution code and output clearly visible.
- User does not want persistence.
- User wants monitor files under the current loommux project.
- User requested subagent execution with no forked context and a 30 minute wait.

## Open Decisions For Executor

None for Batch 0/1.

Implementation choices that must be justified if changed:

- Queue size target around 1000 events.
- Short HTTP timeout target no more than 500 ms per background attempt.
- `httpx` preferred if adding an HTTP dependency.
