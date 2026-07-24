# `run_cell` Freeform Input Contract

The target authority for this contract is [Loommux Cell Control Directive
Design](ipython-mcp-cell-control-directive-design.md). This companion document
records the stable MCP input boundary without creating a second control language.

## Input

`run_cell(freeform)` accepts exactly one loommux IPython cell. It has no
structured `code`, `wait`, `timeout`, or `full_output` argument. Ordinary
Python source uses the default initial wait of 10 seconds and no complete-output
request.

When an author needs to declare submission policy, the cell contains one or more
column-zero control comments:

```python
# loommux: --wait 120 --full-output
build_report()
```

`--wait` accepts one positive finite decimal value. `--full-output` requests
complete terminal combined-output delivery. The parser rejects unknown,
duplicated, missing, malformed, or non-positive options as
`invalid_loommux_directive` before execution allocation and kernel submission.

Distinct options may be declared on separate control comments. Active directive
lines are transport-only metadata: the adapter resolves policy, deletes their
complete physical lines, and then submits the clean source. They do not enter
IPython history, a cell-magic body, or an execution record. This permits a
directive to precede `%%bash` or another cell magic without padding before `%`.

## Apply Patch Transport

A valid Apply Patch program in an outer triple-double-quoted Python literal may
be converted into an equivalent Python `str` expression. The conversion is
strictly limited to validated `*** Begin Patch` / `*** End Patch` programs.
Its relationship to source fidelity and diagnostics is defined by the
[Apply Patch Literal Transform Design](ipython-mcp-protected-multiline-string-design.md).

## Follow-up

A call whose initial wait expires retains its integer `execution` record. Use
`wait`, `execution_status`, `read_output`,
`search_output`, `interrupt`, or `restart` under the
execution-control contract.
