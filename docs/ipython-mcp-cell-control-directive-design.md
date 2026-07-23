# Loommux Cell Control Directive Design

> **Status: Implemented on July 22, 2026.** This document defines the
> `# loommux:` control-directive contract used by `run_python`.

## 1. Decision

Loommux uses comment-shaped, adapter-owned control declarations:

```python
# loommux: --wait 120 --full-output
build_report()
```

The directive controls one Loommux execution. It is not Python runtime state,
not a kernel timeout, and not an IPython magic. The adapter reads it before
submitting the cell; the kernel receives the authored source unchanged unless a
valid Apply Patch literal requires its separate source conversion.

This form intentionally does not occupy the IPython cell-magic slot. A cell may
therefore retain an existing magic such as `%%bash`:

```python
%%bash
# loommux: --wait 120 --full-output
generate-report.sh
```

The first line remains owned by the selected IPython magic. Loommux reads the
directive from the raw freeform input before execution; Bash receives the same
line as its ordinary comment.

The control grammar is a breaking replacement of the former directive language.
Legacy key/value declarations are not translated, merged, or given their former
behavior. Because they use the reserved `# loommux:` namespace but do not use
the option grammar, they fail validation before an execution is allocated.

## 2. Means--Ends Analysis

The target end is a coherent author experience in which Loommux control policy
is explicit without taking ownership of an IPython cell's body interpreter.

```text
End: Express one execution's Loommux policy without reducing IPython composition
|
+- Make control visible and auditable in authored source
|  \- Use complete # loommux: directive lines
|
+- Preserve pre-submission control timing
|  \- Parse directives in the adapter before allocation and kernel submission
|
+- Keep existing %% cell-magic ownership intact
|  \- Do not register or require a Loommux cell magic
|
+- Keep one execution policy unambiguous
|  \- Merge distinct options and reject duplicate or invalid options
|
+- Preserve source facts
|  \- Do not remove, rewrite, or hide directive lines
|
\- Bound exceptional source conversion
   \- Transform only grammar-valid Apply Patch literals
```

The design rejects several tempting means:

- A Loommux cell magic would compete with `%%bash` and every other cell magic
  for the one cell-magic position.
- A kernel-global option would leak a one-execution decision into later cells.
- Structured MCP parameters would split body source from its execution policy.
- Silently accepting legacy directive spellings would retain two public control
  languages.
- A generic Begin/End text protocol would turn the Apply Patch exception into
  an uncontrolled source-language extension point.

## 3. Public Directive Grammar

`run_python(freeform)` accepts ordinary source with zero or more control
directives:

```text
Freeform :=
    { SourceLine }

DirectiveLine :=
    "# loommux:" SP Option { SP Option }

Option :=
    "--wait" SP DecimalLiteral
    | "--full-output"

DecimalLiteral :=
    [1-9][0-9]*
    | [0-9]+\.[0-9]+
```

`DecimalLiteral` is lexical. Its resolved floating-point value must be finite
and greater than zero; for example, `0.0` matches the lexical form but fails
semantic validation.

A directive occupies a complete physical line beginning at column zero. A line
that begins with the reserved prefix is a Loommux directive, not an ordinary
free-form comment. It must contain at least one option and use exactly one
space between tokens.

Canonical forms include:

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

The two independent options may also be written on distinct directive lines:

```python
# loommux: --wait 120
# loommux: --full-output
build_report()
```

An ordinary cell with no directive retains the default initial wait of 10
seconds and does not request complete terminal output.

## 4. Aggregation and Option Semantics

All valid directives in one freeform are collected before execution and
resolved into one immutable policy.

| Declarations in one freeform | Initial wait | Complete terminal output |
| --- | ---: | --- |
| none | 10 seconds | no |
| `--wait 120` | 120 seconds | no |
| `--full-output` | 10 seconds | yes |
| distinct `--wait` and `--full-output` directives | specified value | yes |
| both options on one directive | specified value | yes |

`--wait SECONDS` controls only how long the originating `run_python` call
waits for a terminal result. If that wait expires, the execution remains
running and is observed through `wait_python`, `python_execution_status`,
`read_python_output`, `search_python_output`, `interrupt_python`, or
`reset_python`.

`--full-output` records a per-execution request. Once that execution is
terminal, `run_python` or a later `wait_python` returns its complete combined
output even when the output has more than the normal 300-line threshold. It
does not return a complete running transcript and does not change log storage
or the output-reader tools.

## 5. Strict Validation

One execution must have one unambiguous control policy. The following inputs
fail before Loommux allocates an execution or submits source to the kernel:

| Input | Failure |
| --- | --- |
| `# loommux:` | directive has no option |
| `# loommux: --wait` | missing wait value |
| `# loommux: --wait 0` | non-positive wait |
| `# loommux: --wait -1` | invalid decimal literal |
| `# loommux: --wait infinity` | invalid decimal literal |
| `# loommux: --unknown` | unknown option |
| `# loommux: --wait 20 --wait 30` | duplicate wait option |
| two directives each containing `--wait` | duplicate wait option |
| repeated `--full-output` | duplicate full-output option |
| legacy key/value spelling | unknown option |

The canonical failure surface is:

```text
invalid_loommux_directive
```

An invalid directive has all of these observable properties:

| Property | Required behavior |
| --- | --- |
| Execution allocation | No execution is allocated. |
| Public sequence | The Loommux execution sequence does not advance. |
| Kernel | No source is submitted. |
| Current/recent selection | Existing selection state is unchanged. |
| Output | No execution output is created. |
| Error message | Identifies the directive grammar or option failure concisely. |

No malformed, duplicated, or legacy declaration falls back to default policy.

## 6. Submission and Source-Fidelity Lifecycle

```text
1. The MCP client calls run_python(freeform).
2. Loommux prepares any valid Apply Patch literal conversion.
3. Loommux parses active # loommux: directives from the prepared source.
4. Loommux validates and resolves one control policy.
5. Loommux creates an Execution record.
6. Loommux submits the source to the selected kernel or existing cell magic.
7. Loommux collects IOPub events and waits at most the resolved initial window.
8. Loommux projects terminal output using the resolved full-output request.
```

Directive interpretation is adapter-owned and complete before step 5. The
adapter never waits for kernel code, a body interpreter, or a downstream cell
magic to report options back.

Directive lines are authored facts. For a cell without an Apply Patch literal:

```text
author_source == submitted_source
```

Both source values retain every `# loommux:` line. Loommux does not delete,
blank, move, or conceal them to simplify kernel execution.

Resolved metadata records:

```json
{
  "initial_wait_seconds": 120.0,
  "full_output_requested": true,
  "control_directives": [
    "# loommux: --wait 120",
    "# loommux: --full-output"
  ]
}
```

Monitor events may expose the same facts, but monitoring is not an alternate
control channel and may not infer policy after submission.

## 7. Composition Boundary

The adapter recognizes directive lines in raw freeform source. A selected cell
magic remains responsible for interpreting its own body.

For Python and Bash, `# loommux:` is naturally a comment line:

```python
%%bash
# loommux: --wait 0.1 --full-output
sleep 0.3
printf 'finished\n'
```

Loommux captures the policy before submission. Bash ignores the directive as a
comment and executes the remaining shell body. This proves that Loommux control
does not consume the `%%bash` slot.

The directive syntax does not promise that `#` is inert in every conceivable
downstream language. If a cell magic's body language does not accept `#`
comments, that body language determines whether the authored line is valid.
Loommux does not rewrite source to manufacture cross-language comment
compatibility.

For ordinary Python source, directive-shaped text inside a string literal is
data rather than control:

```python
# loommux: --wait 2
payload = """
# loommux: --full-output
"""
print(payload)
```

Only the outer directive sets the execution policy. The string content does not
request complete output.

## 8. Apply Patch Literal Transform Scope

The Apply Patch literal transform exists solely to transport a grammar-valid
Apply Patch program through ordinary Python source when normal quoting cannot
represent the authored payload safely.

It is not a generic protected-text system. Its exact outer markers are:

```text
*** Begin Patch
...
*** End Patch
```

The payload must satisfy the Apply Patch grammar. Its column-zero structural
lines include:

```text
*** Begin Patch
*** Add File: ...
*** Update File: ...
*** Delete File: ...
*** Move to: ...
@@ ...
*** End of File
*** End Patch
```

Hunk content lines begin with a space, `+`, or `-`. Marker-shaped text with an
invalid patch body is not converted.

A valid Apply Patch program cannot contain an active `# loommux:` directive at
column zero: such a line is neither an Apply Patch structural line nor a valid
hunk-content line. No special directive exception is needed for valid patch
payloads.

Apply Patch conversion is the only accepted reason for `author_source` and
`submitted_source` to differ. It preserves author source and physical line
mapping for diagnostics.

## 9. Black-Box Acceptance Criteria

The implementation is accepted only when public adapter or real-MCP behavior
demonstrates every criterion below.

1. A normal Python cell with `# loommux: --wait 0.1` returns running after the
   initial window without interrupting the kernel.
2. A normal Python cell with `# loommux: --full-output` returns all 301 lines
   of a terminal long output.
3. Split directives and an equivalent combined directive resolve to identical
   execution metadata and output behavior.
4. Duplicate options across one or multiple directive lines fail before
   execution allocation.
5. Invalid directives do not advance the public execution sequence or submit
   their Python body.
6. Directive lines remain in non-patch submitted source.
7. A directive does not create a Python namespace variable or an extra output
   event.
8. A `%%bash` cell with a directive after the Bash magic preserves Bash
   execution, Loommux wait behavior, and complete-output behavior.
9. Python string data containing a directive-shaped line does not change policy.
10. Valid Apply Patch literals retain their value; malformed candidates are not
    transformed; the transform does not change outer directive policy.
11. Former key/value spellings fail as invalid directives and never execute
    their body.
12. Reset, interrupt, rich display, stream ordering, and output readers retain
    their existing behavior for directive-bearing executions.

## 10. Completion Standards

### Definition of Done

The change is complete only when:

1. `# loommux: --wait ...` and `# loommux: --full-output` are the only public
   control syntax.
2. All former Loommux cell-magic runtime registration, relay code, tests,
   examples, and documentation are removed.
3. The adapter aggregates and validates directives before execution allocation.
4. `Execution` and monitor metadata expose resolved `control_directives`.
5. Apply Patch conversion remains narrow and grammar-validated.
6. README, MCP tool descriptions, and all linked design documents agree.
7. The complete test suite, project coverage gate, Ruff, Basedpyright, and
   `git diff --check` pass.

### Definition of Excellent

The implementation is excellent only when Definition of Done is satisfied and:

1. The directive parser has direct coverage for every grammar and aggregation
   branch.
2. Real-kernel tests prove Python, Bash composition, long output, display,
   traceback, interrupt, and reset behavior.
3. Non-patch source equality is checked byte-for-byte.
4. Apply Patch tests reject malformed candidates rather than accepting
   marker-shaped text heuristically.
5. Repository-wide search returns zero former cell-magic symbol, status,
   metadata-field, and kernel-relay references.

## 11. Document Relationships

This document is the authority for `run_python` control declarations. It
complements:

- [run_python Freeform Input Contract](ipython-mcp-freeform-run-python-design.md);
- [IPython MCP Complete Output Directive Design](ipython-mcp-full-output-directive-design.md);
- [IPython MCP Apply Patch Literal Design](ipython-mcp-protected-multiline-string-design.md);
- [Coding Agent 控制面设计](coding-agent-control-plane-design.md);
- [workspace configuration](workspace-configuration.md).

Those documents must use this directive contract and must not present the
former Loommux cell magic as a current or compatible surface.
