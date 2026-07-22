"""Parse adapter-owned Loommux control directives from one submitted cell."""

from __future__ import annotations

import io
import math
import re
import tokenize
from dataclasses import dataclass

DEFAULT_INITIAL_WAIT_SECONDS = 10.0
_DIRECTIVE_PREFIX = "# loommux:"
# ``0`` is admitted solely so it can receive the specified non-positive error
# rather than being indistinguishable from an arbitrary malformed token.
_DECIMAL_LITERAL_RE = re.compile(r"(?:0|[1-9][0-9]*|[0-9]+\.[0-9]+)\Z")


@dataclass(frozen=True)
class LoommuxCellControl:
    """Resolved, immutable control facts for one accepted author cell."""

    initial_wait_seconds: float
    full_output_requested: bool
    control_directives: tuple[str, ...]


class LoommuxDirectiveError(ValueError):
    """A concise, safe-to-return validation failure for ``# loommux:``."""


def parse_loommux_cell_control(author_source: str) -> LoommuxCellControl:
    """Resolve every active ``# loommux:`` declaration without touching the kernel.

    Python-string lines are excluded when the complete source is ordinary Python.
    A cell magic body is opaque to Python tokenization, so its directives are
    intentionally recognized as raw physical comment lines for the body language
    to consume or ignore according to its own syntax.
    """

    wait_seconds = DEFAULT_INITIAL_WAIT_SECONDS
    full_output_requested = False
    wait_seen = False
    directives: list[str] = []
    string_lines = _python_string_lines(author_source)
    for line_number, line in enumerate(author_source.splitlines(), start=1):
        if line_number in string_lines or not line.startswith(_DIRECTIVE_PREFIX):
            continue
        directives.append(line)
        suffix = line[len(_DIRECTIVE_PREFIX) :]
        if not suffix:
            raise LoommuxDirectiveError("# loommux: requires at least one option")
        if not suffix.startswith(" ") or suffix.startswith("  ") or suffix.endswith(" "):
            raise LoommuxDirectiveError("options must be separated by one space")
        wait_seconds, full_output_requested, wait_seen = _parse_options(
            suffix[1:].split(" "),
            wait_seconds,
            full_output_requested,
            wait_seen,
        )
    return LoommuxCellControl(wait_seconds, full_output_requested, tuple(directives))


def _parse_options(tokens: list[str], wait_seconds: float, full_output_requested: bool, wait_seen: bool) -> tuple[float, bool, bool]:
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--wait":
            if wait_seen:
                raise LoommuxDirectiveError("--wait may be specified at most once")
            if index + 1 >= len(tokens):
                raise LoommuxDirectiveError("--wait requires one positive finite decimal value")
            value = tokens[index + 1]
            if _DECIMAL_LITERAL_RE.fullmatch(value) is None:
                raise LoommuxDirectiveError(f"invalid --wait value {value!r}")
            parsed = float(value)
            if not math.isfinite(parsed) or parsed <= 0:
                raise LoommuxDirectiveError("--wait requires one positive finite decimal value")
            wait_seconds = parsed
            wait_seen = True
            index += 2
        elif token == "--full-output":
            if full_output_requested:
                raise LoommuxDirectiveError("--full-output may be specified at most once")
            full_output_requested = True
            index += 1
        else:
            raise LoommuxDirectiveError(f"unknown option {token!r}")
    return wait_seconds, full_output_requested, wait_seen


def _python_string_lines(source: str) -> frozenset[int]:
    """Return Python string-token lines, unless a cell magic owns the body."""

    if source.startswith("%%"):
        return frozenset()
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        return frozenset(
            line_number
            for token in tokens
            if token.type == tokenize.STRING
            for line_number in range(token.start[0], token.end[0] + 1)
        )
    except tokenize.TokenError:
        # Invalid Python will subsequently receive its normal kernel syntax
        # failure. Control parsing must not invent a second source grammar.
        return frozenset()
