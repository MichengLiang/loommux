"""Read the ``%%loommux`` header that may open a Loommux cell.

The header is ordinary IPython cell-magic syntax and has two readers. The
daemon answers the submitting ``run_cell`` call before the kernel finishes, so
it reads ``--wait``, ``--silence`` and ``--full-output`` here; the kernel
extension interprets the cell. Both use this one parser, so they cannot disagree
about the grammar.
"""

from __future__ import annotations

import argparse
import math
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NoReturn

from IPython.core.inputtransformer2 import leading_empty_lines, leading_indent

from loommux.identity import label_problem

HEADER_MAGIC = "loommux"
_HEADER_PREFIX = f"%%{HEADER_MAGIC}"
DEFAULT_INITIAL_WAIT_SECONDS = 10.0


class CellHeaderError(ValueError):
    """A concise, safe-to-return validation failure for ``%%loommux``."""


@dataclass(frozen=True)
class CellHeader:
    """Which kernel interprets one cell, and how the submitting call observes it.

    ``kernel`` is ``None`` when the header names no kernel, which means the
    kernel that received the cell. ``attach`` names the main kernel the
    submitting MCP session works in from this cell on; only the daemon acts
    on it, and the two are never both set. ``create`` lets ``attach`` open a
    main kernel that does not exist; it is never set without ``attach``.
    """

    wait_seconds: float = DEFAULT_INITIAL_WAIT_SECONDS
    full_output: bool = False
    kernel: str | None = None
    attach: str | None = None
    create: bool = False
    # None: the call waits only for the end or wait_seconds (silence-design 4.4).
    silence_seconds: float | None = None


class _HeaderParser(argparse.ArgumentParser):
    # argparse reports errors by printing usage and exiting the process, which
    # would end the daemon or the kernel; a header error is a value to return.
    def error(self, message: str) -> NoReturn:
        raise CellHeaderError(message)


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        seconds = math.nan
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError(f"expected a positive finite number of seconds, got {value!r}")
    return seconds


def _kernel_name(value: str) -> str:
    if (problem := label_problem(value, "kernel name")) is not None:
        raise argparse.ArgumentTypeError(problem)
    return value


# Options only, spelled out in full. A positional kernel name turned every
# word without dashes into a forward (`%%loommux full-output` would open a
# kernel named full-output), and argparse's prefix matching would quietly read
# `--wai 5` as `--wait 5`; with neither, a slip of the pen is an error.
_PARSER = _HeaderParser(prog=_HEADER_PREFIX, add_help=False, allow_abbrev=False)
# --kernel interprets this one cell elsewhere; --attach moves the session.
# Both name where the cell runs, so one header cannot say both.
_TARGET = _PARSER.add_mutually_exclusive_group()
_TARGET.add_argument("--kernel", type=_kernel_name, metavar="NAME")
_TARGET.add_argument("--attach", type=_kernel_name, metavar="NAME")
# Creation is a separate switch on attach, as in tmux new-session -A and
# zellij attach --create: an attach that silently opened an empty kernel would
# hide that the one the agent meant is gone.
_PARSER.add_argument("--create", action="store_true")
_PARSER.add_argument("--wait", type=_positive_seconds, default=DEFAULT_INITIAL_WAIT_SECONDS, metavar="SECONDS")
# A second condition on the same wait, never a switch that changes --wait: a cell
# printing forever never goes quiet, and the call would lose its bound.
_PARSER.add_argument("--silence", type=_positive_seconds, default=None, metavar="SECONDS")
_PARSER.add_argument("--full-output", action="store_true")


def header_arguments(line: str) -> str | None:
    """Return the argument text when ``line`` is a header, else ``None``.

    ``%%loommux?`` is IPython's help request for the magic, not a header, so
    the name must be followed by whitespace or the end of the line.
    """
    if not line.startswith(_HEADER_PREFIX):
        return None
    rest = line[len(_HEADER_PREFIX) :]
    if rest and not rest[0].isspace():
        return None
    return rest.strip()


def parse_header_arguments(arguments: str) -> CellHeader:
    try:
        tokens = shlex.split(arguments)
    except ValueError as exc:
        raise CellHeaderError(str(exc)) from exc
    options = _PARSER.parse_args(tokens)
    if options.create and options.attach is None:
        raise CellHeaderError("--create applies only to --attach NAME")
    return CellHeader(wait_seconds=options.wait, full_output=options.full_output, kernel=options.kernel, attach=options.attach, create=options.create, silence_seconds=options.silence)


def first_cell_line(lines: Sequence[str]) -> str | None:
    """The line IPython examines for a cell magic.

    IPython's own cleanup transforms decide it, so a header indented or
    preceded by blank lines is a header for both readers or for neither.
    """
    cleaned = leading_indent(leading_empty_lines(list(lines)))
    return cleaned[0] if cleaned else None


def read_cell_header(source: str) -> CellHeader:
    """Validate the header of ``source``; a cell without one gets the defaults."""
    line = first_cell_line(source.splitlines(keepends=True))
    arguments = None if line is None else header_arguments(line)
    return CellHeader() if arguments is None else parse_header_arguments(arguments)


HEADER_HELP = f"""Declare which kernel interprets this cell and how it is observed.

Write it as the first line of a cell:

    {_HEADER_PREFIX} [--kernel NAME | --attach NAME [--create]] [--wait SECONDS] [--silence SECONDS] [--full-output]

--kernel NAME    run the rest of the cell in the kernel with this name or id,
                 a child kernel of this home opened on first use; its output
                 is relayed here and its errors end this cell. Omitted: this
                 kernel.
--attach NAME    the MCP session works in the existing main kernel NAME from
                 this cell on, letting go of its current one
--create         with --attach, open NAME when no main kernel has that name
--wait SECONDS   wait up to SECONDS for this cell before run_cell returns
                 (default {DEFAULT_INITIAL_WAIT_SECONDS:g}); an expired wait leaves the cell running
--silence SECONDS
                 also return once the cell has printed nothing for SECONDS,
                 leaving it running; whichever of the two comes first
--full-output    deliver the complete combined output when the cell ends

The options describe this one cell only, except that --attach moves the
session. Another cell magic such as %%bash may follow on the next line.
"""
