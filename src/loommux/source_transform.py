from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_OPENING_LITERAL_RE = re.compile(r'(?<![A-Za-z0-9_])(?P<prefix>(?:[rR]?[fF]?|[fF]?[rR]?))(?P<quote>""")(?P<newline>\r\n|\r|\n)')
_BEGIN_LINE_RE = re.compile(r"^\*\*\* Begin[^\r\n]*$")
_END_LINE_RE = re.compile(r"^\*\*\* End[^\r\n]*$")
_CLOSING_LITERAL_RE = re.compile(r'^[ \t]*(?P<quote>""")')
_LINE_BREAK_RE = re.compile(r"\r\n|\r|\n")
_STRING_START_RE = re.compile(r"(?<![A-Za-z0-9_])(?P<prefix>(?:[rR][bB]|[bB][rR]|[rR][fF]|[fF][rR]|[rRuUbBfF])?)(?P<quote>\"\"\"|'''|'|\")")


@dataclass(frozen=True)
class SourcePosition:
    """A zero-based source coordinate; end coordinates are exclusive."""

    line: int
    column: int

    def as_dict(self) -> dict[str, int]:
        return {"line": self.line, "column": self.column}


@dataclass(frozen=True)
class SourceRange:
    """One half-open source range expressed in physical line coordinates."""

    start: SourcePosition
    end: SourcePosition

    def as_dict(self) -> dict[str, dict[str, int]]:
        return {"start": self.start.as_dict(), "end": self.end.as_dict()}


@dataclass(frozen=True)
class ProtectedLiteral:
    author_start: int
    author_end: int
    submitted_start: int
    submitted_end: int
    author_range: SourceRange
    submitted_range: SourceRange


@dataclass(frozen=True)
class ProtectionTransform:
    """The deterministic input-preparation record for one author cell."""

    author_source: str
    submitted_source: str
    applied: bool
    literal_count: int
    author_ranges: tuple[SourceRange, ...]
    submitted_ranges: tuple[SourceRange, ...]
    line_map: tuple[dict[str, int], ...]

    @property
    def protected_line_numbers(self) -> frozenset[int]:
        lines: set[int] = set()
        for source_range in self.author_ranges:
            lines.update(range(source_range.start.line, source_range.end.line + 1))
        return frozenset(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "author_source": self.author_source,
            "submitted_source": self.submitted_source,
            "applied": self.applied,
            "literal_count": self.literal_count,
            "author_ranges": [source_range.as_dict() for source_range in self.author_ranges],
            "submitted_ranges": [source_range.as_dict() for source_range in self.submitted_ranges],
            "line_map": list(self.line_map),
        }


def prepare_protected_multiline_strings(author_source: str) -> ProtectionTransform:
    """Convert complete protected literals while preserving later line numbers.

    Blank-line padding occupies the removed literal's physical lines. It is not
    part of the replacement expression, but it keeps diagnostics after a long
    literal at the same line number in both author and submitted source.
    """

    line_starts = _line_starts(author_source)
    literals = _find_complete_literals(author_source, line_starts)
    if not literals:
        return _identity_transform(author_source)

    submitted_parts: list[str] = []
    cursor = 0
    submitted_cursor = 0
    completed: list[ProtectedLiteral] = []
    for author_start, author_end, value in literals:
        unchanged = author_source[cursor:author_start]
        submitted_parts.append(unchanged)
        submitted_cursor += len(unchanged)
        replacement = repr(value)
        submitted_start = submitted_cursor
        submitted_parts.append(replacement)
        submitted_cursor += len(replacement)
        submitted_end = submitted_cursor

        # The replaced range includes every separator before the closing quote.
        # Re-emitting only those separators leaves the following source line in
        # place without introducing a second statement on the expression line.
        padding = "".join(_LINE_BREAK_RE.findall(author_source[author_start:author_end]))
        submitted_parts.append(padding)
        submitted_cursor += len(padding)
        submitted_prefix = "".join(submitted_parts)
        completed.append(
            ProtectedLiteral(
                author_start=author_start,
                author_end=author_end,
                submitted_start=submitted_start,
                submitted_end=submitted_end,
                author_range=_range_for_offsets(author_source, line_starts, author_start, author_end),
                submitted_range=SourceRange(
                    start=_position_for_offset(submitted_prefix, submitted_start),
                    end=_position_for_offset(submitted_prefix, submitted_end),
                ),
            )
        )
        cursor = author_end
    submitted_parts.append(author_source[cursor:])
    submitted_source = "".join(submitted_parts)
    author_ranges = tuple(literal.author_range for literal in completed)
    submitted_ranges = tuple(literal.submitted_range for literal in completed)
    return ProtectionTransform(
        author_source=author_source,
        submitted_source=submitted_source,
        applied=True,
        literal_count=len(completed),
        author_ranges=author_ranges,
        submitted_ranges=submitted_ranges,
        line_map=_identity_line_map(author_source),
    )


def _find_complete_literals(source: str, line_starts: tuple[int, ...]) -> list[tuple[int, int, str]] | None:
    literals: list[tuple[int, int, str]] = []
    cursor = 0
    while cursor < len(source):
        if source[cursor] == "#":
            cursor = _line_end(source, cursor)
            continue
        if (string_start := _STRING_START_RE.match(source, cursor)) is None:
            cursor += 1
            continue
        opening = _OPENING_LITERAL_RE.match(source, cursor)
        if opening is not None:
            opening_end = opening.end()
            begin_line = _line_index_for_offset(line_starts, opening_end)
            if begin_line < len(line_starts) and _BEGIN_LINE_RE.fullmatch(_line_text(source, line_starts, begin_line)):
                end_line = _first_end_line(source, line_starts, begin_line + 1)
                if end_line is None or end_line + 1 >= len(line_starts):
                    return None
                closing_start = line_starts[end_line + 1]
                closing_line_end = line_starts[end_line + 2] if end_line + 2 < len(line_starts) else len(source)
                closing = _CLOSING_LITERAL_RE.match(source[closing_start:closing_line_end])
                if closing is None:
                    return None

                closing_end = closing_start + closing.start("quote") + 3
                literals.append((opening.start(), closing_end, source[opening.start("newline"):closing_start]))
                cursor = closing_end
                continue
        cursor = _skip_ordinary_string(source, string_start)
    return literals


def _first_end_line(source: str, line_starts: tuple[int, ...], first_line: int) -> int | None:
    for line in range(first_line, len(line_starts)):
        if _END_LINE_RE.fullmatch(_line_text(source, line_starts, line)):
            return line
    return None


def _line_starts(source: str) -> tuple[int, ...]:
    if not source:
        return ()
    starts = [0]
    starts.extend(match.end() for match in _LINE_BREAK_RE.finditer(source) if match.end() < len(source))
    return tuple(starts)


def _line_end(source: str, start: int) -> int:
    match = _LINE_BREAK_RE.search(source, start)
    return len(source) if match is None else match.end()


def _skip_ordinary_string(source: str, opening: re.Match[str]) -> int:
    quote = opening.group("quote")
    is_triple = len(quote) == 3
    cursor = opening.end()
    while cursor < len(source):
        if source.startswith(quote, cursor):
            return cursor + len(quote)
        if source[cursor] == "\\" and cursor + 1 < len(source):
            cursor += 2
        elif not is_triple and source[cursor] in "\r\n":
            return _line_end(source, cursor)
        else:
            cursor += 1
    return len(source)


def _line_text(source: str, line_starts: tuple[int, ...], line: int) -> str:
    start = line_starts[line]
    end = line_starts[line + 1] if line + 1 < len(line_starts) else len(source)
    return source[start:end].rstrip("\r\n")


def _line_index_for_offset(line_starts: tuple[int, ...], offset: int) -> int:
    for index, _start in enumerate(line_starts):
        if index + 1 == len(line_starts) or offset < line_starts[index + 1]:
            return index
    return len(line_starts)


def _range_for_offsets(source: str, line_starts: tuple[int, ...], start: int, end: int) -> SourceRange:
    return SourceRange(start=_position_for_offset(source, start), end=_position_for_offset(source, end))


def _position_for_offset(source: str, offset: int) -> SourcePosition:
    before = source[:offset]
    line = len(_LINE_BREAK_RE.findall(before))
    last_break = next(reversed(list(_LINE_BREAK_RE.finditer(before))), None)
    column = offset if last_break is None else offset - last_break.end()
    return SourcePosition(line=line, column=column)


def _identity_transform(source: str) -> ProtectionTransform:
    return ProtectionTransform(
        author_source=source,
        submitted_source=source,
        applied=False,
        literal_count=0,
        author_ranges=(),
        submitted_ranges=(),
        line_map=_identity_line_map(source),
    )


def _identity_line_map(source: str) -> tuple[dict[str, int], ...]:
    return tuple({"author_line": line, "submitted_line": line} for line in range(len(_line_starts(source))))
