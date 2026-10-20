"""Prepare one caller-authored cell for execution by a persistent kernel.

This module is the only place that combines the cell header with the Apply
Patch literal transform. The session receives the resulting source and policy;
it does not need to know how either input language was recognized.
"""

from dataclasses import dataclass

from loommux.submission.apply_patch_literals import prepare_apply_patch_literals
from loommux.submission.header import read_cell_header


@dataclass(frozen=True)
class PreparedRunCell:
    """Transient boundary between MCP transport input and IPython source.

    The header stays in ``kernel_source``: it is IPython that the kernel
    interprets, and its history records it as written.
    """

    kernel_source: str
    initial_wait_seconds: float
    full_output_requested: bool


def prepare_run_cell(freeform: object) -> PreparedRunCell:
    """Read the header's observation policy before preparing Python source."""

    if not isinstance(freeform, str):
        raise TypeError("freeform must be a string")
    header = read_cell_header(freeform)
    apply_patch = prepare_apply_patch_literals(freeform)
    return PreparedRunCell(
        kernel_source=apply_patch.submitted_source,
        initial_wait_seconds=header.wait_seconds,
        full_output_requested=header.full_output,
    )
