"""Prepare one caller-authored cell for execution by a persistent kernel.

This module is the only place that combines directive consumption with the
Apply Patch literal transform. The session receives the resulting source and
policy; it does not need to know how either input language was recognized.
"""

from dataclasses import dataclass

from loommux.submission.apply_patch_literals import prepare_apply_patch_literals
from loommux.submission.directives import (
    remove_active_directive_lines,
    scan_active_loommux_directives,
)


@dataclass(frozen=True)
class PreparedRunCell:
    """Transient boundary between MCP transport input and IPython source."""

    kernel_source: str
    initial_wait_seconds: float
    full_output_requested: bool


def prepare_run_cell(freeform: object) -> PreparedRunCell:
    """Validate and consume control metadata before preparing Python source."""

    if not isinstance(freeform, str):
        raise TypeError("freeform must be a string")
    scan = scan_active_loommux_directives(freeform)
    source_without_directives = remove_active_directive_lines(freeform, scan.active_directive_ranges)
    apply_patch = prepare_apply_patch_literals(source_without_directives)
    return PreparedRunCell(
        kernel_source=apply_patch.submitted_source,
        initial_wait_seconds=scan.initial_wait_seconds,
        full_output_requested=scan.full_output_requested,
    )
