# Apply Patch Literal Transform Design

The cell-control authority is [Loommux Cell Control Directive
Design](ipython-mcp-cell-control-directive-design.md). This document defines
the single permitted source-fidelity exception: Apply Patch literal transport.

## Purpose and Boundary

An outer triple-double-quoted Python literal may contain a valid Apply Patch
program:

````python
patch = r"""
*** Begin Patch
*** Update File: example.py
@@
+message = r"""
+你好呀
+"""
*** End Patch
"""
````

Loommux converts the complete outer literal into an equivalent ordinary Python
`str` expression only when the payload validates as an Apply Patch program.
The conversion permits patch payloads to contain Python-awkward quotes,
backslashes, and braces while preserving their value.

This is not a generic Begin/End protocol. The exact outer markers are
`*** Begin Patch` and `*** End Patch`. Valid bodies contain Apply Patch file
operations (`Add File`, `Update File`, `Delete File`, optional `Move to`),
update hunks, and correctly prefixed hunk content. Marker-shaped text with an
invalid body remains ordinary Python source and is never transformed.

## Source Facts

Apply Patch conversion is transient request preparation. Its transform details
exist only while Loommux derives the source sent to IPython; an `Execution`
retains lifecycle, output, displays, and the private delivery policy needed by a
later `wait`, not request source or transform metadata. Conversion padding still
preserves meaningful Python coordinates within the submitted source after a
converted literal.

Active Loommux control directives are removed before this conversion. A valid
Apply Patch payload cannot contain an active `# loommux:` directive at physical
column zero, because it is neither a patch control nor a valid hunk-content
line.

## Verification

Tests must prove valid conversion and value preservation; rejection of malformed
marker-shaped text and ordinary triple-quoted strings; preservation of
non-directive source after control directives are consumed; physical-line
continuity; and independence between Apply Patch transport and the outer
cell-control policy.
