"""Classify and consume directives owned by the cell submission boundary."""

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
class SourceRange:
    """A half-open character range for one complete directive line."""

    start: int
    end: int


@dataclass(frozen=True)
class LoommuxDirectiveScan:
    """Resolved control policy and the exact active source ranges that own it."""

    initial_wait_seconds: float
    full_output_requested: bool
    active_directive_ranges: tuple[SourceRange, ...]


class LoommuxDirectiveError(ValueError):
    """A concise, safe-to-return validation failure for ``# loommux:``."""


def scan_active_loommux_directives(source: str) -> LoommuxDirectiveScan:
    """Resolve active directives and return their deletion ranges.

    The ranges are the single source of truth for both validation and removal.
    A magic body is opaque to Python tokenization, but a leading directive may
    precede the ``%%`` line that establishes that body.
    """

    lines = _physical_lines(source)
    string_lines = frozenset() if _is_cell_magic_after_candidate_removal(lines) else _python_string_lines(source)

    wait_seconds = DEFAULT_INITIAL_WAIT_SECONDS
    full_output_requested = False
    wait_seen = False
    active_ranges: list[SourceRange] = []
    for line_number, (line, source_range) in enumerate(lines, start=1):
        if line_number in string_lines or not line.startswith(_DIRECTIVE_PREFIX):
            continue
        active_ranges.append(source_range)
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
    return LoommuxDirectiveScan(wait_seconds, full_output_requested, tuple(active_ranges))


def remove_active_directive_lines(source: str, ranges: tuple[SourceRange, ...]) -> str:
    """Delete validated directive lines, including each owned terminator.

    Control declarations are transport metadata. They must not become IPython
    history, downstream magic input, or implicit source-coordinate padding.
    """

    parts: list[str] = []
    cursor = 0
    for source_range in ranges:
        parts.append(source[cursor : source_range.start])
        cursor = source_range.end
    parts.append(source[cursor:])
    return "".join(parts)


def _physical_lines(source: str) -> tuple[tuple[str, SourceRange], ...]:
    lines: list[tuple[str, SourceRange]] = []
    cursor = 0
    for line_with_ending in source.splitlines(keepends=True):
        end = cursor + len(line_with_ending)
        lines.append((line_with_ending.rstrip("\r\n"), SourceRange(cursor, end)))
        cursor = end
    if cursor < len(source):
        lines.append((source[cursor:], SourceRange(cursor, len(source))))
    return tuple(lines)


def _is_cell_magic_after_candidate_removal(lines: tuple[tuple[str, SourceRange], ...]) -> bool:
    """Detect a magic after temporarily removing only directive candidates."""

    for line, _source_range in lines:
        if line.startswith(_DIRECTIVE_PREFIX) or not line:
            continue
        return line.startswith("%%")
    return False


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
    """Return physical lines occupied by Python string tokens."""

    try:
        # ``tokenize`` does not treat a lone CR from StringIO as Python's
        # universal-newline input does. Normalizing this private classification
        # view preserves physical line numbers while keeping CR source bytes
        # untouched for range deletion and kernel submission.
        token_source = source.replace("\r\n", "\n").replace("\r", "\n")
        fstring_start = getattr(tokenize, "FSTRING_START", None)
        fstring_end = getattr(tokenize, "FSTRING_END", None)
        fstring_starts: list[int] = []
        string_lines: set[int] = set()
        for token in tokenize.generate_tokens(io.StringIO(token_source).readline):
            if token.type == tokenize.STRING:
                string_lines.update(range(token.start[0], token.end[0] + 1))
            elif token.type == fstring_start:
                fstring_starts.append(token.start[0])
            elif token.type == fstring_end and fstring_starts:
                string_lines.update(range(fstring_starts.pop(), token.end[0] + 1))
        return frozenset(string_lines)
    except (IndentationError, tokenize.TokenError):
        # Invalid Python gets its normal kernel syntax failure. Directive parsing
        # must not introduce another language parser for otherwise ordinary code.
        return frozenset()
