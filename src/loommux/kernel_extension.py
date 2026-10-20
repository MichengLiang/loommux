"""Interpret the ``%%loommux`` cell header inside a Loommux kernel.

Every Loommux kernel loads this IPython extension at start. A header that
names this kernel is removed while IPython transforms the cell, so the body
runs as the cell itself. It cannot be a cell magic that runs the body: a cell
magic is synchronous, its only way to run a body is a nested ``run_cell``,
and that cannot run top-level ``await`` while the kernel's loop is running
the outer cell.
"""

from __future__ import annotations

from IPython.core.error import UsageError
from IPython.core.interactiveshell import InteractiveShell

from loommux.daemon import Daemon
from loommux.submission.header import HEADER_HELP, HEADER_MAGIC, CellHeaderError, header_arguments, parse_header_arguments


def remove_own_header(lines: list[str]) -> list[str]:
    """Dissolve the leading headers that name this kernel.

    IPython may transform one cell more than once, for example to check that
    it is complete, so this stays a pure function of its lines. An invalid
    header is left in place for the cell magic, which reports it.

    Each header becomes an empty line, so traceback line numbers count lines of
    the cell as it was written. A body that is itself a cell magic, such as
    ``%%bash`` or a header naming another kernel, must start the cell for
    IPython to recognize it; its line numbers belong to its own interpreter.
    """
    count = 0
    while count < len(lines) and (arguments := header_arguments(lines[count])) is not None:
        try:
            parse_header_arguments(arguments)
        except CellHeaderError:
            break
        count += 1
    body = lines[count:]
    if count == 0 or (body and body[0].startswith("%%")):
        return body
    return ["\n"] * count + body


def _header_magic(line: str, cell: str) -> None:
    try:
        parse_header_arguments(line)
    except CellHeaderError as exc:
        raise UsageError(f"invalid %%{HEADER_MAGIC} header: {exc}") from None
    raise UsageError(
        f"this %%{HEADER_MAGIC} header names this kernel, which interprets it while transforming a cell; "
        "run_cell_magic cannot run that body because it cannot run top-level await. Submit the body as a cell."
    )


_header_magic.__doc__ = HEADER_HELP


def _misplaced_header(line: str) -> None:
    raise UsageError(f"%%{HEADER_MAGIC} must be the first line of the cell")


def _single_percent_header(line: str) -> None:
    raise UsageError(f"write the header as %%{HEADER_MAGIC}, with two percent signs, on the first line of the cell")


def load_ipython_extension(ip: InteractiveShell) -> None:
    if remove_own_header not in ip.input_transformers_cleanup:
        ip.input_transformers_cleanup.append(remove_own_header)
    # Hidden from %who: it belongs to the kernel, not to the author's work.
    ip.push({"loommux": Daemon()}, interactive=False)
    magics = ip.magics_manager
    magics.register_function(_header_magic, "cell", HEADER_MAGIC)
    # IPython reads ``%%loommux`` on a later line as a line magic whose name
    # keeps one percent sign, so this name catches exactly that mistake.
    magics.register_function(_misplaced_header, "line", f"%{HEADER_MAGIC}")
    magics.register_function(_single_percent_header, "line", HEADER_MAGIC)


def unload_ipython_extension(ip: InteractiveShell) -> None:
    if remove_own_header in ip.input_transformers_cleanup:
        ip.input_transformers_cleanup.remove(remove_own_header)
