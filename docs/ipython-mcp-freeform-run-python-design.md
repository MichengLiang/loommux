# `run_python` Freeform Input Contract

This document is the authority for the `run_python` freeform input and timeout
directive grammar. Protected multiline raw-string syntax, source conversion,
and directive regions are defined by
[IPython MCP Protected Multiline Raw String Design](ipython-mcp-protected-multiline-string-design.md).
Execution identity, lifecycle, output streams, result surfaces, and all
follow-up tools are defined by
[Coding Agent Control Plane Design](coding-agent-control-plane-design.md).
The no-value full-output marker and its result-delivery behavior are defined by
[IPython MCP Complete Output Directive Design](ipython-mcp-full-output-directive-design.md).

## Input

`run_python(freeform)` accepts one loommux Python cell as its only input.
Ordinary cell source is submitted to the persistent IPython kernel; complete
protected multiline raw strings are converted to equivalent Python `str`
expressions before submission. It does not accept a structured `code` field or
a `timeout_seconds` field.

Protected strings use `*** Begin...` and `*** End...` lines at physical column
zero. Their complete syntax, raw-value semantics, and relationship to the
directives in this document are defined by the protected multiline string
design.

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

Tests must cover exact directive recognition, protected-string directive
regions, default fallback, source conversion, non-persistence, continued
running after timeout, and the integer execution coordinate exposed at the real
MCP boundary.
