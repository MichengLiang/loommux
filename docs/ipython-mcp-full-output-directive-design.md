# IPython MCP Complete Output Control Design

The cell-control authority is [Loommux `%%loommux` Cell Control Magic
Design](ipython-mcp-cell-control-magic-design.md). This document narrows its
focus to complete terminal combined-output delivery.

## Author Surface

An author requests complete terminal combined output with the first-line cell
magic:

```python
%%loommux --full-output
print("\n".join(generate_manifest()))
```

It may be combined with an initial wait:

```python
%%loommux --wait 120 --full-output
build_report()
```

The adapter validates the declaration before allocating an execution and stores
the resolved `full_output_requested` fact on that record. The private kernel
does not interpret the option.

## Delivery Behavior

Without `--full-output`, terminal combined output exceeding 300 lines remains
stored but is omitted from `run_python` and `wait_python`. With the option, a
terminal response returns the entire combined stream regardless of its line
count. A running execution still returns the normal running surface; callers
can use output-reading tools for partial progress.

The option is per execution. It survives a later `wait_python`, error,
interrupt, or reset-induced `killed` state, but it does not alter
`read_python_output`, `search_python_output`, output-log storage, or later
cells.

## Verification

Real-MCP tests must prove complete 301-line delivery, normal combined ordering
of stdout, stderr, execute results, rich displays, and tracebacks, plus
continuity through delayed completion and reset. Tool descriptions must present
only the `%%loommux --full-output` surface.
