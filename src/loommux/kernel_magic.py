"""The deliberately small private-kernel relay for ``%%loommux``."""

from __future__ import annotations

from IPython.core.getipython import get_ipython
from IPython.core.interactiveshell import InteractiveShell
from IPython.core.magic import register_cell_magic


def register_loommux_magic() -> None:
    """Register the body-only relay for each private kernel lifetime."""

    shell = get_ipython()
    if not isinstance(shell, InteractiveShell):
        raise RuntimeError("%%loommux requires an InteractiveShell")

    @register_cell_magic("loommux")
    def loommux(_line: str, cell: str) -> None:
        # The adapter has already interpreted the authored option line before
        # allocation. One leading newline restores body traceback coordinates to
        # the author's physical cell, where the magic occupies line one.
        shell.run_cell(f"\n{cell}", store_history=False)
