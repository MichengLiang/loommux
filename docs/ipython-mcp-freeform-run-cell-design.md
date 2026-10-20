# `run_cell` Freeform Input Contract

The cell-control authority is [Cell Header Design](cell-header-design.md).
This companion document records the stable MCP input boundary without creating
a second control language.

## Input

`run_cell(freeform)` accepts exactly one IPython cell. It has no structured
`code`, `wait`, `timeout`, or `full_output` argument. A cell without a header
uses the default initial wait of 10 seconds and no complete-output request.

When an author needs to control one cell, its first line is a `%%loommux`
header:

```python
%%loommux --wait 120 --full-output
build_report()
```

`--wait` accepts one positive finite number of seconds. `--full-output`
requests complete terminal combined-output delivery. An invalid header returns
`invalid_cell_header` before execution allocation and kernel submission.

The daemon reads the header's options and submits the cell unchanged. The
kernel interprets the header as IPython, so it may precede `%%bash` or another
cell magic, and IPython history records it as written.

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
