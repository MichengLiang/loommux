# IPython MCP Complete Output Control Design

The cell-control authority is [Loommux Cell Control Directive
Design](ipython-mcp-cell-control-directive-design.md). This document narrows
its focus to complete terminal combined-output delivery.

## Author Surface

An author requests complete terminal combined output with a control directive:

```python
# loommux: --full-output
print("\n".join(generate_manifest()))
```

It may be combined with an initial wait:

```python
# loommux: --wait 120 --full-output
build_report()
```

The adapter validates and consumes all directives before allocating an
execution. It retains the resolved complete-output policy as private runtime
state only because a later `wait` needs it; the selected body interpreter and
public response data do not receive the option.

## Delivery Behavior

Without `--full-output`, terminal combined output exceeding 300 lines remains
stored but is omitted from `run_cell` and `wait`. With the option, a
terminal response returns the entire combined stream regardless of its line
count. A running execution still returns the normal running surface; callers
can use output-reading tools for partial progress.

An omitted-output notice identifies the retained normalized combined text by
its total lines, Unicode code point characters, and UTF-8 size. Its human
readable size uses one binary unit selected from `B`, `KiB`, `MiB`, and larger
units; structured responses retain the exact UTF-8 byte count.

The option is per execution. It survives a later `wait`, error,
interrupt, or reset-induced `killed` state, but it does not alter
`read_output`, `search_output`, output-log storage, or later
cells.

## Verification

Real-MCP tests must prove complete 301-line delivery, normal combined ordering
of stdout, stderr, execute results, rich displays, and tracebacks, plus
continuity through delayed completion and reset. Tool descriptions must present
only the `# loommux: --full-output` surface.
