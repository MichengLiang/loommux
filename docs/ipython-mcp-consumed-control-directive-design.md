# Loommux Transient Control Directive Consumption Design

> **Status: Implemented on July 24, 2026.** This is the implementation rationale
> for the released control-directive behavior. The user-facing
> [Loommux Cell Control Directive Design](ipython-mcp-cell-control-directive-design.md)
> is the grammar authority; its companion documents use the same consumption and
> execution-ownership rules.

## 1. Decision

`# loommux:` lines are transport-only control declarations. Loommux must parse,
validate, consume, and completely remove every active declaration before it
submits source to IPython.

The submitted source must contain neither directive text nor placeholder blank
lines for removed directives. Consequently, Loommux directives must not appear
in IPython input history, kernel namespace state, downstream cell-magic bodies,
or retained execution metadata.

For example, this MCP input:

```python
# loommux: --wait 120
# loommux: --full-output
%%bash
set -euo pipefail
uv run pytest
```

must cause the kernel to receive exactly:

```bash
%%bash
set -euo pipefail
uv run pytest
```

The leading `%` must be the first character of submitted source. In particular,
the implementation must not submit leading newlines, spaces, comments, or
sentinel values in place of removed directives.

## 2. Problem

Loommux currently recognizes `# loommux:` directives before it submits a cell,
but retains those lines in the source passed to IPython. This gives
transport-level metadata an incorrect second life as executable-cell text and
IPython history.

The most visible failure occurs when a directive precedes a cell magic:

```python
# loommux: --wait 120
%%bash
set -euo pipefail
uv run pytest
```

IPython requires `%%bash` to be at the beginning of a cell. With the comment
still present, it no longer recognizes `%%bash` as a cell magic. It instead
transforms it as a line magic and attempts to parse the following Bash text as
Python, causing a syntax error.

Keeping blank lines after directive removal avoids that one failure but is not a
valid design. Nothing in the public Loommux contract consumes a mapping from
the source supplied to `run_cell` to the source received by IPython:

- IPython needs only the clean cell source.
- The kernel needs only the clean cell source.
- Loommux has consumed the control options before submission.
- MCP clients already own the original tool request and tool result.
- No MCP response exposes a source map or promises traceback coordinates in
  the original `freeform` request.

Blank-line retention would therefore create an undocumented, implicit
coordinate convention while leaving history noise behind. If a future client
requires source-coordinate translation, that requirement must introduce a
separate explicit protocol. It must not be implemented by preserving invisible
padding in kernel input.

## 3. Scope

This change applies to the eight-tool IPython MCP control plane, especially the
`run_cell(freeform)` entrypoint and its execution lifecycle.

### 3.1 Goals

The implementation must:

1. Consume valid Loommux control directives before any kernel submission.
2. Remove complete active directive lines without replacement text.
3. Allow directives before `%%` cell magics.
4. Prevent directives from entering IPython history.
5. Preserve the current directive grammar and validation failures.
6. Preserve `--wait` behavior for the originating `run_cell` call.
7. Preserve `--full-output` behavior across a later `wait` call.
8. Retain only execution state that has a live runtime consumer.
9. Make source preparation transient and execution lifecycle state persistent.
10. Replace every documentation and test assertion that describes directive
    retention as a requirement.

### 3.2 Non-goals

This change must not:

- add structured `wait`, `timeout`, `full_output`, or `code` parameters to
  `run_cell`;
- change the `freeform` input field;
- introduce a Loommux IPython cell magic;
- add a source-map API;
- preserve old execution metadata fields as compatibility aliases;
- make Loommux a client-side audit store;
- change workspace selection, kernel startup, output-stream storage, or the
  meanings of `read_output`, `search_output`, `interrupt`, and `restart`;
- broaden the Apply Patch literal grammar.

## 4. Terminology And Ownership

The following terms are normative in this document.

| Term | Meaning | Owner | Lifetime | Reaches IPython |
| --- | --- | --- | --- | --- |
| `freeform request` | The original string supplied to `run_cell`. | MCP client | Client trace and request handling | No |
| `directive` | A valid active `# loommux:` line. | Adapter preparation | One preparation operation | No |
| `control policy` | Resolved `--wait` and `--full-output` values. | Adapter and execution runtime | See below | No |
| `clean source` | Source after directive removal and Apply Patch preparation. | Kernel submission path | One kernel submission and IPython history | Yes |
| `execution` | Loommux lifecycle and output record addressed by an integer. | Adapter | Server-process lifetime | Not as source text |

`--wait` is consumed when `run_cell` waits for its initial result. Its resolved
numeric value has no consumer after that call returns and must not be retained
on an `Execution`.

`--full-output` must survive as private execution runtime state because a
running execution may later be observed through `wait`. The later `wait` call
 must know whether terminal combined output is exempt from the normal 5,000-token
delivery threshold. The implementation may choose the private field name, but
it must preserve this behavior without exposing the state as a public response
field.

MCP clients, agent traces, gateway logs, and model-provider request records are
the appropriate places to audit the original tool request. Loommux execution
records are not request archives.

## 5. Public Contract

### 5.1 Input

The public input remains:

```text
run_cell(freeform: string)
```

The directive grammar remains:

```text
DirectiveLine :=
    "# loommux:" SP Option { SP Option }

Option :=
    "--wait" SP DecimalLiteral
    | "--full-output"
```

The current validation rules remain normative:

- a directive begins at physical column zero;
- it must contain one or more options;
- options use exactly one separating space;
- `--wait` appears at most once and takes a positive finite decimal;
- `--full-output` appears at most once;
- unknown, duplicate, missing, malformed, non-finite, and non-positive values
  fail with `invalid_loommux_directive`;
- an invalid directive allocates no execution and submits no source.

### 5.2 Active Directive Classification

Not every substring that looks like a directive is active control syntax.
Loommux must classify active lines before removal:

1. A candidate begins with `# loommux:` at physical column zero.
2. For ordinary Python source, a candidate within a Python string token is
   string data and is not active.
3. For a cell magic, the body language is opaque to Python tokenization. A
   candidate physical line in that cell is active Loommux control syntax.
4. A directive may occur before the cell magic that it controls. The
   implementation must determine cell-magic classification from a temporary
   view in which candidate Loommux lines are removed. That view is a cell magic
   only when its first remaining non-empty physical line begins with `%%` at
   column zero. This allows a leading directive followed by `%%bash` to
   classify as a cell magic.
5. A normal non-Loommux comment before `%%bash` remains ordinary user source.
   Loommux must not remove or relocate it. If that makes the IPython cell magic
   invalid, it is not a Loommux control-directive concern.

The fourth rule is needed only to recognize a magic whose first source line is
otherwise a Loommux directive. Any remaining non-empty line before `%%`,
including an ordinary comment, prevents magic classification. The rule must not
turn arbitrary comments or ordinary Python into a magic cell.

### 5.3 Kernel Source

For every accepted `run_cell` request, the kernel source is the clean source:

```text
clean source =
    prepare_apply_patch_literals(
        remove_active_directive_lines(freeform request)
    ).submitted_source
```

`remove_active_directive_lines` must delete the exact source range of each
active directive, including its own line terminator when one exists. It must:

- insert no blank line, comment, marker, or padding;
- retain all non-directive bytes in their original order;
- retain original line terminators of remaining source;
- delete a final directive without a terminator by deleting only its text;
- leave Python string data that resembles a directive untouched;
- run only after directive validation succeeds.

The kernel source must not be reconstructed from parsed options. Removal must
operate on source ranges produced by the same classifier that validated the
directives. Separate parser and remover matching rules would allow drift and
are prohibited.

### 5.4 Response Surface

The following fields are control-request details and must no longer appear in
`run_cell`, `wait`, or `execution_status` response data:

```text
initial_wait_seconds
full_output_requested
control_directives
```

No compatibility aliases may be added. A client that needs to know what it
requested already owns its MCP tool request.

The existing public fields for execution identity, lifecycle status, output
omission, output text, stream data, errors, timestamps, and kernel status
remain governed by their respective contracts.

## 6. Source Preparation Algorithm

The implementation must make source preparation a named, testable operation
rather than distribute it across `run_cell`, a context variable, and
`Execution` construction.

The exact algorithm is:

```text
prepare_run_cell(freeform):
    reject a non-string freeform value

    scan = scan_active_loommux_directives(freeform)
    validate scan options
    if validation fails:
        return invalid_loommux_directive

    source_without_directives =
        delete_exact_ranges(freeform, scan.active_directive_ranges)

    apply_patch =
        prepare_apply_patch_literals(source_without_directives)

    return PreparedRunCell(
        kernel_source=apply_patch.submitted_source,
        initial_wait_seconds=scan.initial_wait_seconds,
        full_output_requested=scan.full_output_requested,
    )
```

`PreparedRunCell` is preparation-local data. It must not be added to the
public MCP schema and must not be stored on a completed execution.

The order is intentional:

1. Directive removal first establishes the clean programming-language source
   that IPython should see.
2. Apply Patch conversion then works only on programming-language source.
3. The execution lifecycle receives only `kernel_source` and the one delivery
   flag that remains behaviorally necessary.

A valid Apply Patch body cannot contain an active directive under the current
grammar. Removing outer directives before patch preparation therefore does not
broaden the patch grammar or reinterpret patch payload text.

## 7. State And Lifecycle Changes

### 7.1 Adapter

`IPythonMCPAdapter.run_cell` must:

1. prepare the request using the algorithm in section 6;
2. return `invalid_loommux_directive` before execution allocation when
   validation fails;
3. allocate an execution only after preparation succeeds;
4. submit exactly `PreparedRunCell.kernel_source`;
5. use `PreparedRunCell.initial_wait_seconds` only for the originating wait;
6. keep `PreparedRunCell.full_output_requested` only as private execution
   delivery policy.

The current `_pending_execution_input` context variable exists to retain
original input and Apply Patch metadata for execution construction. It must be
removed. No source or request-archive context is required after preparation
returns a `PreparedRunCell`.

### 7.2 Execution

`Execution` must become a lifecycle and output record, not a source archive.
After implementation it must not retain these fields or equivalent aliases:

```text
code
author_source
submitted_source
apply_patch_transform
initial_wait_seconds
control_directives
```

It must retain private full-output delivery state until the execution record is
discarded with the server process. A private `full_output_requested` boolean is
one acceptable representation. That state is required to make a later `wait`
preserve the original delivery behavior.

`Execution.snapshot()` and `Execution.status_snapshot()` must stop serializing
all removed control-request fields.

### 7.3 Kernel Session

`KernelSession.submit` must accept the clean source explicitly, for example:

```python
submit(execution, source)
```

It must pass that source directly to `BlockingKernelClient.execute`. The
session must not recover source from an `Execution` field.

This boundary makes source preparation transient and execution state persistent
by construction.

### 7.4 Apply Patch Transform

`prepare_apply_patch_literals` remains a narrowly scoped preparation helper.
It may retain transform details while its caller is preparing a submission, but
the adapter must not copy those details into `Execution`.

Existing tests for valid patch conversion, payload preservation, malformed
candidate rejection, and ordinary string preservation remain required. Tests
that assert every execution archives source or transform metadata must be
removed or replaced.

## 8. Error Handling And Invariants

The following invariants are mandatory:

| Condition | Required behavior |
| --- | --- |
| `freeform` is not a string | Return `invalid_code`; do not allocate an execution. |
| Directive grammar is invalid | Return `invalid_loommux_directive`; do not remove source, allocate an execution, or submit the kernel. |
| Kernel is busy after successful preparation | Return existing `busy` behavior; do not create a second execution. |
| Kernel submission fails | Preserve existing execution-error handling, but do not expose original directive text. |
| No active directives exist | Submit source unchanged except for the existing valid Apply Patch transform. |
| Active directives exist | Submit source with every active directive fully deleted and no placeholder. |
| `--wait` expires | Keep execution running; do not alter kernel runtime or later calls. |
| `--full-output` execution later reaches terminal state | `wait` returns full combined output even above the normal threshold. |
| Server restarts | Existing output records remain readable; no source or directive archive is resurrected. |

The implementation must not make directive removal best-effort. A recognized
but invalid declaration is an error, while a valid declaration is consumed
completely. There is no path that partially removes declarations and continues.

## 9. Required Code Change Map

This table names the expected implementation surface. It is a checklist, not a
license to make unrelated refactors.

| File | Required change |
| --- | --- |
| `src/loommux/cell_control.py` | Replace raw-directive retention with one classifier that supplies validated policy and exact active ranges. Preserve ordinary Python string exclusion and define magic classification after temporary candidate removal. |
| `src/loommux/adapter.py` | Add the explicit preparation flow; remove source/archive context plumbing; submit only clean source; retain only the private full-output delivery policy required by runtime behavior. |
| `src/loommux/source_transform.py` | Keep Apply Patch conversion narrow and transient. Do not use it as a reason to archive source on `Execution`. |
| `src/loommux/execution.py` | Remove source, transform, initial-wait, and directive-archive fields; remove their response serialization; keep only lifecycle/output state and private full-output policy. |
| `src/loommux/kernel_session.py` | Change submission to receive clean source explicitly rather than read it from an execution record. |
| `src/loommux/mcp_server_factory.py` | Update tool descriptions to state that control directives are consumed before IPython receives the cell. Do not alter the `freeform` parameter. |
| `src/loommux/presentation.py` | Audit every presentation string and remove every statement that suggests control declarations can be inspected later. Leave the file unchanged only when that audit finds no such statement. |
| `src/loommux/mcp_result_policy.py` | Verify that rich-output behavior continues to use only the tool name and runtime execution state, not deleted metadata. |
| `tests/` | Replace retention assertions with removal, clean-history, lifecycle, and response-schema assertions described in section 10. |

## 10. Verification Matrix

All cases below require automated coverage. Tests that only inspect a mocked
adapter variable are insufficient for assertions about IPython parsing or
history; those assertions require a real kernel.

| ID | Scenario | Required proof |
| --- | --- | --- |
| V1 | Ordinary Python with `--wait` | The code executes; the directive is absent from source given to the kernel. |
| V2 | Leading directive before `%%bash` | A real Bash cell magic runs; submitted source begins with `%`, not newline, space, or `#`. |
| V3 | Two leading directives before `%%bash` | Both options take effect; neither directive nor blank placeholder appears in submitted source. |
| V4 | Directive inside a Bash magic body | The directive is consumed; remaining Bash source runs correctly. |
| V5 | Body language that does not accept `#` comments | A test cell magic proves directive removal occurs before that body interpreter receives source. |
| V6 | Python multiline string containing directive-shaped text | The string is unchanged and does not alter control policy. |
| V7 | Invalid directive | No execution number advances, current/recent selection remains unchanged, and no source reaches the kernel. |
| V8 | No directives | Source is unchanged except for the existing validated Apply Patch conversion. |
| V9 | Long output with `--full-output` | A running initial response followed by `wait` returns all 301 or more combined lines. |
| V10 | Long output without `--full-output` | Combined output above 5,000 `o200k_base` tokens is omitted while the complete record remains readable through output tools. |
| V11 | IPython history | A real-kernel history query contains clean source only: no `# loommux:` and no directive-derived leading blank lines. |
| V12 | Apply Patch plus outer directive | The directive is removed, the patch value remains correct, and later executable source still runs. |
| V13 | CRLF input | Active lines and their own CRLF terminators are removed with no added bytes. |
| V14 | Final directive without a trailing newline | The directive text is removed safely and no replacement is added. |
| V15 | Public response data | `run_cell`, `wait`, and `execution_status` do not contain `initial_wait_seconds`, `full_output_requested`, or `control_directives`. |
| V16 | Execution structure | Source/archive fields are absent from an execution record and kernel submission does not depend on them. |
| V17 | Restart and interrupt | Existing interrupt, restart, output-retention, and execution-sequence behavior remains correct for directive-bearing cells. |

The history test must inspect the IPython history facility, such as its raw
input history, rather than infer cleanliness from an adapter-local source
variable. This verifies the product outcome that motivated the change.

## 11. Documentation Migration

Implementation is incomplete until every conflicting document is updated. The
following edits are required in the same change set as the code:

| Document | Required update |
| --- | --- |
| `README.md` | Explain that directives are one-call control metadata consumed before kernel submission. Remove any implication that they are ordinary cell source or retained state. |
| `docs/ipython-mcp-cell-control-directive-design.md` | Replace directive-preservation, source-equality, source-fidelity, and execution-metadata rules with this design's consumption rules. Keep this existing path as the stable user-facing grammar authority and link it to this design as the implementation rationale. |
| `docs/ipython-mcp-freeform-run-cell-design.md` | State that `freeform` carries directives because it is the only input field, but active directives are removed before the cell reaches IPython. |
| `docs/ipython-mcp-full-output-directive-design.md` | Keep the per-execution delivery behavior while clarifying that the resolved flag is private runtime state, not a public response field or downstream interpreter instruction. |
| `docs/ipython-mcp-protected-multiline-string-design.md` | Remove the claim that every execution archives `author_source`, `submitted_source`, and transform details. Describe Apply Patch preparation as transient. |
| `docs/coding-agent-control-plane-design.md` | Remove any execution-record or source-retention claims that conflict with this design. |
| `src/loommux/mcp_server_factory.py` docstrings | Use the same vocabulary as the user-facing documents. |

All Markdown cross-references must resolve after any file move or authority
change. The final documentation must not leave two current documents with
opposite statements about whether directives reach IPython.

## 12. Completion Standard

The implementation is complete only when all of the following are true:

1. Every active valid directive is fully removed before kernel submission.
2. The source passed to a leading-directive `%%bash` request begins with
   `%%bash`, without padding.
3. Real IPython history contains no Loommux directive text or removal padding.
4. Python-string data that resembles a directive remains untouched.
5. Invalid directives produce no execution allocation or kernel submission.
6. `--wait` and `--full-output` retain their existing user-visible behavior.
7. A delayed `wait` retains complete-output behavior through terminal success,
   error, interruption, and restart-induced kill.
8. Execution records and public response data do not retain the removed request
   archive fields.
9. All documents listed in section 11 agree with the implementation.
10. Repository-wide search finds no stale claim that directives are retained in
    submitted source, IPython history, or execution metadata.
11. `uv run pytest`, the configured coverage gate, `uv run ruff check .`,
    `uv run basedpyright`, and `git diff --check` pass.

Passing only existing tests is not sufficient. Tests must be changed where they
currently encode the superseded retention behavior.

## 13. Excellence Standard

The implementation is excellent only when it first satisfies every completion
requirement and also satisfies all of these:

1. One classification result supplies both directive validation and exact
   deletion ranges; no duplicated matching logic exists.
2. The preparation operation is independently unit-tested and has a name that
   expresses its boundary between freeform transport input and kernel source.
3. Real-kernel tests prove both cell-magic recognition and clean IPython
   history, not merely an adapter-local approximation.
4. CRLF, final-no-newline, multiple-directive, string-literal, magic-body, and
   invalid-directive edge cases have direct tests.
5. The adapter no longer contains a context variable or execution fields whose
   only former purpose was to archive request source.
6. Comments explain why directives are consumed before kernel submission and
   why full-output policy alone survives as private execution state. They do
   not narrate obvious control flow.
7. The diff remains scoped to control preparation, execution ownership,
   documentation, and directly affected tests; unrelated output, workspace,
   or kernel-lifecycle refactors are absent.
8. A reviewer can map every requirement in sections 5 through 11 to an
   implementation location and an automated verification case without relying
   on chat history or unstated assumptions.

## 14. Explicit Rejections

The following approaches are intentionally rejected:

| Rejected approach | Reason |
| --- | --- |
| Preserve directives in submitted source | Breaks leading cell magics and pollutes IPython history. |
| Replace directives with blank lines | Creates history noise and an undocumented coordinate convention with no consumer. |
| Move directives after `%%magic` | Changes source order, still leaks metadata to body interpreters, and fails for DSLs where `#` is not a comment. |
| Add a Loommux cell magic | Competes for IPython's single cell-magic slot and prevents composition with `%%bash` and other magics. |
| Add structured control fields now | Changes the established Freeform input contract and is outside this change. |
| Archive original source on `Execution` | Duplicates data already owned by the MCP client without a runtime consumer. |
| Keep response-field aliases | Extends a misleading public model and obscures completion of the cleanup. |
| Add source-map padding | Solves no current public requirement; any future mapping need must be explicit. |

## 15. Implementation Review Questions

Before declaring the change complete, the reviewer must be able to answer
"yes" to every question:

1. Can a developer submit a leading Loommux directive followed by `%%bash`
   without a syntax error?
2. Does the kernel receive no directive text and no directive-derived padding?
3. Can a user retrieve IPython history without finding Loommux transport
   metadata?
4. Can a directive-shaped Python string remain literal data?
5. Can an invalid directive avoid every kernel and execution side effect?
6. Can a later `wait` still deliver complete output requested by the original
   `run_cell`?
7. Are execution records lifecycle records rather than hidden MCP request
   archives?
8. Are all user-facing documents consistent with the resulting behavior?
9. Does the code make the ownership boundary evident without relying on
   reviewer inference?

Any "no", "not tested", or "it should" answer means the implementation has not
met the Completion Standard.
