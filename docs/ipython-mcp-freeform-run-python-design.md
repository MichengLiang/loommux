# `run_python` Freeform Input Contract

This document is the authority for the `run_python` freeform input and timeout
directive grammar. Execution identity, lifecycle, output streams, result
surfaces, and all follow-up tools are defined by
[IPython MCP Execution Control Plane Design](ipython-mcp-execution-control-plane-design.md).
The no-value full-output marker and its result-delivery behavior are defined by
[IPython MCP Complete Output Directive Design](ipython-mcp-full-output-directive-design.md).

## Input

`run_python(freeform)` accepts one raw Python cell as its only input. The text
is submitted unchanged to the persistent IPython kernel. It does not accept a
structured `code` field or a `timeout_seconds` field.

## Timeout Directive

One complete directive line selects the wait duration for that invocation:

```python
# loommux: timeout_seconds=120
```

The complete-line grammar is:

```text
^# loommux: timeout_seconds=([1-9][0-9]*|[0-9]+\.[0-9]+)$
```

A unique valid directive uses its positive finite decimal value. No valid
directive, an invalid directive, or multiple valid directives uses the
10-second default. The directive controls only how long this MCP call waits;
it neither limits Python runtime nor interrupts the cell, changes later calls,
or creates runtime variables.

## Follow-up

A timed-out invocation remains one integer `execution` record. Use
`wait_python`, `python_execution_status`, `read_python_output`,
`search_python_output`, `interrupt_python`, or `reset_python` according to the
control-plane contract. Readers select `execution` plus a stream; there is no
log-address parameter or alternate execution identifier.

## Verification

Tests must cover exact directive recognition, default fallback, raw source
submission, non-persistence, continued running after timeout, and the integer
execution coordinate exposed at the real MCP boundary.
