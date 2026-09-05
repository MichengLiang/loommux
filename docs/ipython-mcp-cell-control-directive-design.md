# Loommux Cell Control Directive Design

> **Status: Implemented on July 24, 2026.** This document is the user-facing
> authority for `# loommux:` declarations accepted by `run_cell`.

## 1. Decision

Loommux uses comment-shaped, adapter-owned control declarations:

```python
# loommux: --wait 120 --full-output
build_report()
```

They control one Loommux submission. They are neither Python runtime state nor
IPython source. Loommux scans, validates, and consumes every active declaration
before it sends the clean cell to IPython. The original `freeform` request
remains owned by the MCP client; Loommux execution records are lifecycle and
output records, not request archives.

This allows a directive to precede a cell magic without consuming the magic
slot or leaving padding before it:

```python
# loommux: --wait 120
%%bash
uv run pytest
```

IPython receives exactly `%%bash\nuv run pytest`. The directive is absent from
IPython history and from the Bash body.

## 2. Public Grammar

`run_cell(freeform)` accepts ordinary source with zero or more directives:

```text
DirectiveLine :=
    "# loommux:" SP Option { SP Option }

Option :=
    "--wait" SP DecimalLiteral
    | "--full-output"
```

Each declaration begins at physical column zero and contains at least one
option. Separating tokens requires exactly one ASCII space. `--wait` may occur
once and must resolve to a positive, finite decimal. `--full-output` may occur
once. Unknown, duplicate, missing, malformed, non-finite, and non-positive
values fail with `invalid_loommux_directive` before an execution is allocated
or source is submitted.

Canonical forms are:

```python
# loommux: --wait 120
build_report()
```

```python
# loommux: --full-output
print("\n".join(generate_manifest()))
```

```python
# loommux: --wait 120 --full-output
build_report()
```

Distinct options may appear on separate directive lines. Legacy key/value
spellings remain invalid; Loommux does not translate or merge them.

## 3. Active Lines

Only active declarations are consumed. A column-zero candidate inside an
ordinary Python string token is string data and remains unchanged:

```python
# loommux: --wait 2
payload = """
# loommux: --full-output
"""
print(payload)
```

The outer line supplies the wait policy; the string does not request complete
output.

Cell-magic bodies are opaque to Python tokenization. Loommux first forms a
temporary view with candidate directive lines removed. If that view's first
remaining non-empty physical line begins with `%%` at column zero, the cell is
a magic cell and every candidate in its body is active transport syntax. This
also recognizes directives before the `%%` line. An ordinary comment before a
magic remains ordinary user source and is never removed or relocated.

## 4. Submission Lifecycle

The adapter performs the following named preparation operation before it can
allocate an execution:

```text
prepare_run_cell(freeform):
    reject non-string input
    scan active directives and validate their options
    delete each active directive range, including its own terminator
    prepare valid Apply Patch literals in the remaining source
    return clean kernel source, initial wait, and full-output policy
```

Deletion preserves every non-directive byte and its original ordering. It does
not add blank lines, comments, sentinels, or source-coordinate padding. CRLF
terminators are deleted with their directive, and a final directive without a
terminator deletes only its own text. The scan result supplies both validation
and deletion ranges, so the two operations cannot drift apart.

`--wait` is used only by the originating `run_cell` call. `--full-output` is
kept as private runtime state because a later `wait` must still decide whether
to omit a terminal combined log above 5,000 tokens. Neither fact appears in
`run_cell`, `wait`, or `execution_status` data.

The [Apply Patch Literal Transform Design](ipython-mcp-protected-multiline-string-design.md)
continues to apply after directive removal. Its transform details are
preparation-local and are not retained on an `Execution`.

## 5. Observable Behavior

| Condition | Required behavior |
| --- | --- |
| No active directive | Submit source unchanged except for a valid Apply Patch conversion. |
| Valid directive | Submit source with every active directive fully deleted. |
| Invalid directive | Return `invalid_loommux_directive`; do not allocate or submit. |
| Initial wait expires | Keep the execution running for later observation. |
| Terminal `--full-output` execution | Return all combined output from `run_cell` or a later `wait`. |
| Directive before `%%bash` | IPython recognizes the cell magic because submitted source begins with `%`. |
| Python string resembling a directive | Preserve it as literal data. |

The public response surface contains execution identity, status, timestamps,
kernel metadata, output, omissions, and errors. It does not contain
`initial_wait_seconds`, `full_output_requested`, `control_directives`, source,
or Apply Patch transform metadata.

## 6. Scope And Relationships

This contract keeps the established one-field `run_cell(freeform)` input. It
does not add structured wait, timeout, full-output, code, or source-map
parameters, and it does not introduce a Loommux IPython magic.

It complements the [run_cell Freeform Input Contract](ipython-mcp-freeform-run-cell-design.md),
the [complete-output control](ipython-mcp-full-output-directive-design.md), the
[Apply Patch Literal Transform Design](ipython-mcp-protected-multiline-string-design.md),
and the [coding-agent control-plane design](coding-agent-control-plane-design.md).
The [transient-control consumption rationale](ipython-mcp-consumed-control-directive-design.md)
records the implementation-level ownership and verification decisions behind
this user-facing contract.
