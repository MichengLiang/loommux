# Loommux `%%loommux` Cell Control Magic Design

> **Status: Implemented on July 22, 2026.** This document specifies the
> breaking replacement of the former `# loommux:` directives. The implementation
> and related documentation use this contract together.

## 1. Decision

Loommux will replace all comment-shaped `run_python` control directives with
one IPython cell magic:

```python
%%loommux --wait 120 --full-output
build_report()
```

`%%loommux` is the only author-facing control surface for a Loommux execution.
Its options describe the whole cell submission; the remainder of the cell is
ordinary Python body source.

The magic has deliberately split responsibilities:

| Owner | Responsibility |
| --- | --- |
| Loommux adapter | Parse and validate the magic line before kernel submission; create immutable execution control metadata; apply initial wait and complete-output delivery policy. |
| IPython kernel | Accept `%%loommux` as a cell magic and execute its body exactly once. It does not own Loommux control semantics. |
| Execution record | Preserve source and record resolved control facts alongside normal lifecycle and output state. |

The kernel recognizes the authored syntax, but it neither persists the options
nor uses them to decide wait duration or output behavior. Those options belong
to the Loommux control plane, not to the Python namespace or global IPython
configuration.

The replacement is intentionally breaking:

- `# loommux: timeout_seconds=...` is no longer a Loommux directive.
- `# loommux: full_output` is no longer a Loommux marker.
- There is no compatibility parser, warning period, automatic conversion, or
  precedence rule between old and new forms.
- The old comments resume their ordinary Python meaning: they are comments
  only.

## 2. Means--Ends Analysis

The design uses a means--ends analysis: every mechanism exists only because it
advances a stated author or control-plane objective. A convenient mechanism
must not become an accidental product surface.

### 2.1 Current state

The present control inputs are complete Python comment lines:

```python
# loommux: timeout_seconds=120
# loommux: full_output
```

The adapter scans them before submitting source to the kernel. The first affects
only how long the originating `run_python` call waits for an initial result. The
second is stored on the execution record and causes terminal combined output to
bypass the default 300-line delivery threshold. Neither is a Python runtime
timeout, a kernel interrupt deadline, a namespace variable, or a session-wide
setting.

The control-plane behavior is correct, but the author surface is weaker than
its meaning. A normal-looking comment silently controls the MCP call and
result-delivery policy. Its visual form does not reveal that it is a strict
cell-level declaration with material effects.

Existing source preparation is also named and described too broadly as a
protected multiline-string mechanism. Its actual purpose is narrower: safely
carrying a valid Apply Patch program through a Python cell when ordinary
quoting cannot carry the patch content. Treating that exception as a generic
protected-text feature invites unrelated syntax and constraints to accumulate
around it.

### 2.2 Desired state

An author works in a persistent IPython environment. When the author already
knows how the complete cell should be submitted and observed, that intent
appears as a cell-level declaration in the language of the environment:

```python
%%loommux --wait 120 --full-output
build_report()
```

The desired result has five properties:

1. The declaration is visibly distinct from the Python body and visibly applies
   to the complete cell.
2. Loommux learns the options before submission, when it still can decide the
   initial wait and execution delivery metadata.
3. The authored magic line remains a fact in source history; Loommux does not
   remove or rewrite it for internal convenience.
4. The kernel accepts the magic and runs the body without gaining Loommux
   control-plane state or emitting adapter-specific output.
5. The Apply Patch exception stays narrow and verifiable rather than becoming a
   general-purpose text-protection language.

### 2.3 Means--ends hierarchy

```text
End: A coherent IPython-native author experience for Loommux cell submission
|
+- Make cell-level control visually explicit
|  \- Use the `%%loommux` cell-magic surface
|
+- Preserve correct control-plane timing
|  \- Parse and validate options in the adapter before kernel submission
|
+- Preserve source as an execution fact
|  \- Submit authored `%%loommux` source unchanged except for a necessary
|     Apply Patch literal conversion
|
+- Keep Loommux policy out of the kernel
|  \- Give the kernel magic a body-execution role only
|
+- Make declarations single-valued and auditable
|  \- Use strict option grammar and fail before execution allocation on error
|
\- Keep exceptional source conversion bounded
   \- Recognize and convert only valid Apply Patch literals
```

The hierarchy also identifies rejected means:

- A comment directive does not make control scope explicit enough.
- A line magic does not express that the configuration belongs to the whole
  cell.
- A kernel-global variable would make a per-execution policy persist
  accidentally.
- Removing the magic line before submission would erase an authored fact.
- A compatibility layer would preserve two control languages without advancing
  the target experience.
- A generic Begin/End protected-text protocol would make Apply Patch syntax a
  de facto extensibility mechanism.

## 3. Public Cell Syntax

`run_python` accepts either ordinary Python source or a Loommux-controlled
IPython cell:

```text
Freeform :=
    PythonCell
    | LoommuxCell

PythonCell :=
    CellBody

LoommuxCell :=
    "%%loommux" { SP Option } NEWLINE CellBody

Option :=
    "--wait" SP DecimalLiteral
    | "--full-output"
```

`DecimalLiteral` is an unsigned base-10 integer or decimal fraction:

```text
DecimalLiteral :=
    [1-9][0-9]*
    | [0-9]+\.[0-9]+
```

The resolved value must be finite and greater than zero. The lexical grammar
therefore admits a token such as `0.0`, while strict option validation rejects
it as a non-positive wait.

An ordinary Python cell without `%%loommux` retains the existing default policy:
an initial wait of 10 seconds and no complete-output request. A bare
`%%loommux` cell resolves to that same policy while making its cell-level
control boundary explicit.

Canonical author forms are:

```python
%%loommux
value = 1 + 1
value
```

```python
%%loommux --wait 120
build_report()
```

```python
%%loommux --full-output
print("\n".join(generate_manifest()))
```

```python
%%loommux --wait 120 --full-output
build_report()
```

The source line is both an IPython cell magic and an execution-control
declaration. It is not a Python statement and does not create a Python name.
The body after the magic is authored and observed as ordinary Python source.

`run_python` retains its one-argument MCP surface:

```text
run_python(freeform)
```

There is no structured `wait`, `timeout`, `full_output`, or `code` field on
the MCP tool. The cell remains the sole authoring surface for submission policy
and Python body together.

## 4. Option Semantics

### 4.1 `--wait`

`--wait SECONDS` sets the maximum duration that the originating `run_python`
call waits for the selected execution to reach a terminal state. It does not
limit Python runtime and does not interrupt the kernel when the duration
expires.

If the execution is still running when the window expires, `run_python`
returns the normal running surface with the Loommux execution number. The
execution continues. The caller can then use `wait_python`,
`python_execution_status`, `read_python_output`, `search_python_output`,
`interrupt_python`, or `reset_python` under their existing contracts.

The default initial wait remains 10 seconds.

### 4.2 `--full-output`

`--full-output` records a per-execution request for complete terminal combined
output delivery.

Without this option, an execution that reaches a terminal state with more than
300 combined output lines preserves its logs but omits the full body from
`run_python` and `wait_python`. With this option, terminal combined output is
returned in full, regardless of that threshold.

The option does not make running output complete, does not alter output-log
storage, and does not alter `read_python_output` or `search_python_output`.

### 4.3 Resolved defaults

| Cell form | Initial wait | Complete terminal combined output |
| --- | ---: | --- |
| ordinary Python cell, or bare `%%loommux` | 10 seconds | no |
| `--wait 120` | 120 seconds | no |
| `--full-output` | 10 seconds | yes |
| `--wait 120 --full-output` | 120 seconds | yes |

### 4.4 Strict option validation

Every accepted `%%loommux` cell resolves to exactly one control policy. The
following inputs fail before Loommux creates an execution record or submits to
the kernel:

| Input | Failure reason |
| --- | --- |
| `%%loommux --wait` | `--wait` has no value |
| `%%loommux --wait 0` | wait value is not positive |
| `%%loommux --wait -1` | wait value is not in the grammar |
| `%%loommux --wait infinity` | wait value is not finite decimal input |
| `%%loommux --unknown` | unknown option |
| `%%loommux --wait 20 --wait 30` | `--wait` is duplicated |
| `%%loommux --full-output --full-output` | `--full-output` is duplicated |

The canonical failure status is:

```text
invalid_loommux_magic
```

Its message identifies the invalid token or conflicting option without
including unrelated source content. No invalid or ambiguous input falls back to
a default. A control declaration is either valid and single-valued, or it
prevents submission.

## 5. Submission and Execution Lifecycle

The options are needed before kernel execution begins. The lifecycle is:

```text
1. The MCP client calls run_python(freeform).
2. The adapter receives the complete authored source.
3. The adapter identifies and parses the %%loommux magic line.
4. The adapter validates the options.
5. The adapter applies any necessary Apply Patch literal conversion.
6. The adapter creates an Execution record with immutable control metadata.
7. The adapter submits source to the private IPython kernel.
8. The kernel accepts %%loommux and executes its body exactly once.
9. The adapter collects IOPub input, stream, result, display, error, and idle
   events for that execution.
10. The adapter waits at most the resolved initial wait duration.
11. The adapter projects the execution result using the resolved complete-output
    policy.
```

The adapter must parse options before step 6. `--wait` controls step 10, and
`--full-output` must be present on the execution record before terminal output
is projected.

The kernel does not report options back to the adapter. The adapter does not
wait for a magic-side signal. The control plane remains deterministic when an
execution finishes immediately, produces no output, errors, or remains running
after the initial wait window.

## 6. Source Fidelity Invariants

### 6.1 Author source is preserved

`%%loommux` is an authored execution fact. Loommux must not delete, blank,
replace, or conceal the magic line merely because the adapter already parsed
it.

For a cell without an Apply Patch literal conversion:

```text
author_source == submitted_source
```

Both values include the original `%%loommux` line:

```python
%%loommux --wait 120 --full-output
build_report()
```

This invariant applies to execution records, monitor events, input-oriented
presentation, and diagnostic inspection. It makes the control decision
auditable from the original cell rather than reconstructible only from hidden
adapter state.

### 6.2 Apply Patch conversion is the sole source exception

An Apply Patch literal conversion may change `submitted_source`, because its
purpose is to make an otherwise difficult-to-quote patch payload executable as
an equivalent Python string expression. That conversion must preserve author
source separately and maintain author-to-submitted physical-line mapping for
later diagnostics.

The presence of `%%loommux` alone never creates a source transformation.

### 6.3 Python data never becomes control input

Text that merely resembles a magic declaration inside a normal Python string
literal is data, not a Loommux option:

```python
%%loommux
example = """
%%loommux --full-output
"""
print(example)
```

The adapter parses only the authored cell-magic line. The outer magic controls
the execution; the inner text cannot add or modify any control option. No
generic protection protocol or whole-cell directive scan is needed to establish
this boundary.

## 7. Kernel Magic Runtime Contract

The private Loommux kernel must register `%%loommux` for every kernel lifetime,
including the kernel created after `reset_python`.

The magic's runtime contract is intentionally small:

1. It accepts the authored cell-magic syntax.
2. It executes the supplied cell body exactly once.
3. It does not interpret `--wait` or `--full-output`.
4. It does not create, update, or retain namespace state for those options.
5. It does not add stdout, stderr, result text, display data, or monitoring
   events of its own.
6. It preserves normal IOPub ownership and ordering for the body.

The adapter is the sole interpreter of magic options. The kernel magic is a
body-execution relay, not a second control parser.

The relay's implementation technique is deliberately not fixed by this
document. Before implementation is accepted, a focused runtime proof must show
that the chosen technique meets all black-box invariants in Section 12:

- exactly one body execution;
- no duplicate execution count;
- no duplicate `Out[...]` projection;
- normal stdout, stderr, rich-display, and traceback ordering;
- traceback coordinates meaningful to the author;
- normal interrupt and reset behavior.

This keeps an unverified internal mechanism out of the public contract while
making its externally required behavior non-negotiable.

## 8. Apply Patch Literal Transform Scope

### 8.1 Narrow purpose

The former broad protected-multiline-string concept is replaced by an Apply
Patch literal transform.

Its only purpose is to carry a valid Apply Patch program through a Python cell
when ordinary Python quoting cannot represent the authored patch safely. It is
not a generic text container, an arbitrary Begin/End protocol, or an extension
point for future source languages.

### 8.2 Recognized outer markers

Only the exact Apply Patch outer markers participate:

```text
*** Begin Patch
...
*** End Patch
```

Broad forms such as `*** Begin<any suffix>` and `*** End<any suffix>` are not
independent Loommux syntax and do not trigger the transform merely by
appearance.

### 8.3 Valid patch-line classes

The transformed payload must satisfy the Apply Patch grammar. At physical
column zero, its structural lines are limited to patch controls, including:

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

Patch hunk content lines begin with one of:

```text
<space>
+
-
```

The transform must validate against the current Apply Patch grammar rather than
using marker resemblance as a substitute for validation. A candidate with
invalid body lines is ordinary Python source and is not transformed as an Apply
Patch literal.

### 8.4 Relationship to cell magic

A valid Apply Patch program cannot contain a valid `%%loommux` magic line at
physical column zero: that line is neither an Apply Patch structural line nor a
valid hunk content line. Therefore the Apply Patch grammar itself eliminates
the purported conflict between patch payload and cell-control magic.

No magic-placement exception is introduced for Apply Patch payloads. The cell
magic remains the normal first line of the authored cell; a patch literal, when
present, belongs to the Python body.

## 9. Removed Comment Directives

The following source forms have no Loommux control meaning:

```python
# loommux: timeout_seconds=120
# loommux: full_output
```

They are ordinary Python comments. Loommux does not parse, validate, warn
about, migrate, prioritize, or otherwise interpret them.

The implementation removes:

- old directive regular expressions;
- their parser functions;
- protected-region exceptions that exist only for directive scanning;
- directive-specific test fixtures and black-box assertions;
- directive examples from user-facing tool descriptions, README material, and
  design documents.

## 10. Error Surface

`invalid_loommux_magic` is a submission error with these observable properties:

| Property | Required behavior |
| --- | --- |
| Execution allocation | No execution is allocated. |
| Sequence | The public Loommux execution sequence does not advance. |
| Kernel | No code is submitted to the kernel. |
| Current / recent selection | Existing selection state is unchanged. |
| Output | No execution output is created. |
| Message | Identifies the magic grammar or option failure concisely. |

Examples:

```text
invalid_loommux_magic: --wait requires one positive finite decimal value
```

```text
invalid_loommux_magic: --full-output may be specified at most once
```

The error surface must not expose implementation paths, private kernel state,
or unrelated content from the submitted cell.

## 11. Execution Metadata and Monitoring

Every accepted execution records the resolved policy as structured facts. Exact
serialized field names are an implementation detail, but the information model
is fixed:

```json
{
  "initial_wait_seconds": 120.0,
  "full_output_requested": true,
  "control_magic": "%%loommux --wait 120 --full-output"
}
```

The record distinguishes:

- authored magic source;
- resolved wait duration;
- resolved complete-output request;
- normal execution lifecycle and output facts.

Monitoring records may include the same resolved facts, but monitoring is not
an alternate control channel and may not reconstruct policy from heuristics
after submission.

## 12. Black-Box Acceptance Criteria

This section is normative. The implementation is accepted only when every
criterion below is demonstrated through public adapter or real-MCP behavior,
not merely by unit-testing a parser in isolation.

### 12.1 Basic cell execution

```python
%%loommux
print("hello")
```

Must produce the same visible Python output as its ordinary-body equivalent,
without an extra magic result, magic-specific stdout, or namespace variable.

### 12.2 Initial wait behavior

```python
%%loommux --wait 0.1
import time
time.sleep(1)
```

Must return a running Loommux execution after the initial window without
interrupting the kernel. A subsequent `wait_python` must observe the same
execution reaching its normal terminal state.

### 12.3 Complete output behavior

```python
%%loommux --full-output
print("\n".join(f"line-{number}" for number in range(301)))
```

Must return all 301 output lines when the execution becomes terminal, whether
the terminal response is delivered by `run_python` directly or by a later
`wait_python`.

### 12.4 Combined output ordering

```python
%%loommux --full-output
import sys
print("stdout")
print("stderr", file=sys.stderr)
"result"
```

Must preserve existing combined IOPub-derived ordering. The magic itself must
not insert an output event before, between, or after those events.

### 12.5 Exactly-once body execution

```python
%%loommux
counter += 1
counter
```

Must increment `counter` exactly once. This applies to ordinary completion,
exceptional completion, interruption, and reset-adjacent execution paths.

### 12.6 Traceback coordinates

```python
%%loommux
value = 1
raise RuntimeError("expected")
```

Must preserve a traceback location meaningful against the author's physical
cell. The runtime relay may not create a second user-visible execution frame
that obscures the body location.

### 12.7 Source fidelity

For a non-patch cell:

```python
%%loommux --wait 2 --full-output
print("source fact")
```

The execution's author source and submitted source must be identical and both
must include the magic line.

### 12.8 Kernel reset

A cell using `%%loommux` must work before and after `reset_python`. Reset may
replace the kernel process, but it may not remove the magic from the new
private kernel.

### 12.9 Invalid input isolation

```python
%%loommux --wait 10 --wait 20
print("must not run")
```

Must return `invalid_loommux_magic`, leave execution numbering unchanged, and
never emit `must not run`.

### 12.10 String-data isolation

```python
%%loommux
payload = """
%%loommux --full-output
"""
print(payload)
```

Must use only the outer magic's policy. Text inside the Python string must not
be interpreted as an additional declaration.

### 12.11 Apply Patch boundary

The test suite must distinguish all of the following:

1. A valid Apply Patch literal is converted and retains its value.
2. A Begin/End-looking block with invalid patch grammar is not converted.
3. A normal Python triple-quoted string is not an Apply Patch literal.
4. A valid patch grammar cannot accidentally contain a valid Loommux magic
   line.
5. Apply Patch source conversion does not alter outer `%%loommux` control
   semantics.

### 12.12 Removed directive absence

The test suite must prove that old `# loommux:` comments no longer alter
initial-wait or complete-output behavior.

## 13. Definition of Done and Definition of Excellent

### 13.1 Definition of Done

The work is complete only when all conditions hold:

1. `%%loommux`, `--wait`, and `--full-output` implement Sections 3 and 4.
2. Option validation fails before execution allocation and kernel submission.
3. The adapter owns all control interpretation before submission.
4. The private kernel accepts `%%loommux` and executes its body exactly once.
5. `%%loommux` remains present in non-patch submitted source.
6. The old comment parser and all references to its public surface are removed.
7. Apply Patch conversion is narrowed to validated Apply Patch literals.
8. Every black-box criterion in Section 12 passes.
9. The complete project suite passes with the configured coverage gate,
   currently at least 90 percent total coverage.
10. Ruff, Basedpyright, and `git diff --check` pass.
11. README, MCP tool descriptions, and linked design documents describe only
    the new author surface.

### 13.2 Definition of Excellent

An implementation is excellent only when it satisfies Definition of Done and
also demonstrates all of the following:

1. The new magic parser and option validator have 100 percent line coverage
   and direct tests for every validation branch.
2. Real-kernel tests prove the relay preserves stdout, stderr, execute results,
   rich display order, errors, interrupts, reset, and execution identity.
3. Non-patch cells preserve byte-for-byte author/submitted source equality.
4. Traceback checks verify author-meaningful body coordinates rather than
   merely asserting that an error occurred.
5. Apply Patch grammar tests reject malformed candidates instead of accepting
   marker-shaped text heuristically.
6. Public docs have no stale use of “protected multiline raw string” where the
   actual subject is Apply Patch transport.
7. Repository-wide search finds zero old directive examples, parser names, and
   directive-specific contractual text.

## 14. Implementation Change Map

The implementation will touch the following responsibilities. Exact file names
may be adjusted only when the responsibility remains in the same layer.

| Area | Required change |
| --- | --- |
| Adapter input handling | Replace comment scanning with `%%loommux` pre-submission parsing and strict validation. |
| Execution construction | Store resolved initial-wait and full-output facts from the magic. |
| Kernel bootstrap | Register the private-kernel `%%loommux` body-execution relay on every start and reset. |
| Source preparation | Replace the broad protected-string model with validated Apply Patch literal conversion. |
| Execution / monitor projection | Preserve authored magic source and expose resolved policy facts where execution metadata is already observed. |
| Tests | Replace directive fixtures with real-kernel magic tests; add parser, error-isolation, source-fidelity, relay, reset, and Apply Patch-boundary coverage. |
| User documentation | Replace old comment examples in README and MCP tool descriptions with `%%loommux`. |
| Related designs | Update freeform input, complete-output, and former protected-string documents so their contracts agree with this document. |

## 15. Document Relationships

This document is the target authority for the `run_python` cell-control
surface. When implemented, it supersedes the directive portions of:

- [run_python Freeform Input Contract](ipython-mcp-freeform-run-python-design.md);
- [IPython MCP Complete Output Directive Design](ipython-mcp-full-output-directive-design.md);
- [Apply Patch Literal Transform Design](ipython-mcp-protected-multiline-string-design.md).

It complements, rather than redefines:

- [Coding Agent 控制面设计](coding-agent-control-plane-design.md), which owns the
  broader workspace, kernel, execution, and tool-control model;
- [IPython MCP Execution Control Plane Design](ipython-mcp-execution-control-plane-design.md),
  which owns the public tool sequence and output-reading model;
- [IPython MCP Output Surface Design](ipython-mcp-output-surface-design.md),
  which owns result projection and output-surface rules.

When this proposal is implemented, those documents must be edited in the same
change set so no repository document presents the removed comment directives as
current behavior.
