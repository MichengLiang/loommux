# `run_python` Freeform Input Contract

The target authority for this contract is [Loommux Cell Control Directive
Design](ipython-mcp-cell-control-directive-design.md). This companion document
records the stable MCP input boundary without creating a second control language.

## Input

`run_python(freeform)` accepts exactly one loommux Python cell. It has no
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

Distinct options may be declared on separate control comments. Directive lines
are preserved in author and submitted source. The adapter resolves policy before
submission; the selected Python or cell-magic body interpreter receives the
authored source normally.

## Apply Patch Transport

A valid Apply Patch program in an outer triple-double-quoted Python literal may
be converted into an equivalent Python `str` expression. The conversion is
strictly limited to validated `*** Begin Patch` / `*** End Patch` programs.
Its relationship to source fidelity and diagnostics is defined by the
[Apply Patch Literal Transform Design](ipython-mcp-protected-multiline-string-design.md).

## Follow-up

A call whose initial wait expires retains its integer `execution` record. Use
`wait_python`, `python_execution_status`, `read_python_output`,
`search_python_output`, `interrupt_python`, or `reset_python` under the
execution-control contract.
