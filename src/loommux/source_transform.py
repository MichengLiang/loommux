"""Transport valid Apply Patch literals through ordinary Python source."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_OPENING_LITERAL_RE = re.compile(r'(?<![A-Za-z0-9_])(?P<prefix>(?:[rR]?[fF]?|[fF]?[rR]?))(?P<quote>""")(?P<newline>\r\n|\r|\n)')
_CLOSING_LITERAL_RE = re.compile(r'^[ \t]*(?P<quote>""")')
_LINE_BREAK_RE = re.compile(r"\r\n|\r|\n")
_STRING_START_RE = re.compile(r"(?<![A-Za-z0-9_])(?P<prefix>(?:[rR][bB]|[bB][rR]|[rR][fF]|[fF][rR]|[rRuUbBfF])?)(?P<quote>\"\"\"|'''|'|\")")
_BEGIN_PATCH = "*** Begin Patch"
_END_PATCH = "*** End Patch"


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
class ApplyPatchLiteral:
    author_start: int
    author_end: int
    submitted_start: int
    submitted_end: int
    author_range: SourceRange
    submitted_range: SourceRange


@dataclass(frozen=True)
class ApplyPatchTransform:
    """The deterministic input-preparation record for one author cell."""

    author_source: str
    submitted_source: str
    applied: bool
    literal_count: int
    author_ranges: tuple[SourceRange, ...]
    submitted_ranges: tuple[SourceRange, ...]
    line_map: tuple[dict[str, int], ...]

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


def prepare_apply_patch_literals(author_source: str) -> ApplyPatchTransform:
    """Convert only complete, grammar-valid Apply Patch string literals.

    Padding retains physical line numbers after a converted literal, which is
    essential for author-meaningful diagnostics in later Python statements.
    """

    line_starts = _line_starts(author_source)
    literals = _find_apply_patch_literals(author_source, line_starts)
    if not literals:
        return _identity_transform(author_source)

    submitted_parts: list[str] = []
    cursor = 0
    submitted_cursor = 0
    completed: list[ApplyPatchLiteral] = []
    for author_start, author_end, value in literals:
        unchanged = author_source[cursor:author_start]
        submitted_parts.append(unchanged)
        submitted_cursor += len(unchanged)
        replacement = repr(value)
        submitted_start = submitted_cursor
        submitted_parts.append(replacement)
        submitted_cursor += len(replacement)
        submitted_end = submitted_cursor

        padding = "".join(_LINE_BREAK_RE.findall(author_source[author_start:author_end]))
        submitted_parts.append(padding)
        submitted_cursor += len(padding)
        submitted_prefix = "".join(submitted_parts)
        completed.append(
            ApplyPatchLiteral(
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
    return ApplyPatchTransform(
        author_source=author_source,
        submitted_source=submitted_source,
        applied=True,
        literal_count=len(completed),
        author_ranges=tuple(literal.author_range for literal in completed),
        submitted_ranges=tuple(literal.submitted_range for literal in completed),
        line_map=_identity_line_map(author_source),
    )


def _find_apply_patch_literals(source: str, line_starts: tuple[int, ...]) -> list[tuple[int, int, str]]:
    literals: list[tuple[int, int, str]] = []
    cursor = 0
    while cursor < len(source):
        if source[cursor] == "#":
            cursor = _line_end(source, cursor)
            continue
        string_start = _STRING_START_RE.match(source, cursor)
        if string_start is None:
            cursor += 1
            continue
        opening = _OPENING_LITERAL_RE.match(source, cursor)
        if opening is None:
            cursor = _skip_ordinary_string(source, string_start)
            continue

        opening_end = opening.end()
        first_content_line = _line_index_for_offset(line_starts, opening_end)
        end_line = _first_exact_end_patch_line(source, line_starts, first_content_line + 1)
        if (
            first_content_line >= len(line_starts)
            or _line_text(source, line_starts, first_content_line) != _BEGIN_PATCH
            or end_line is None
            or end_line + 1 >= len(line_starts)
        ):
            cursor = _skip_ordinary_string(source, string_start)
            continue
        closing_start = line_starts[end_line + 1]
        closing_line_end = line_starts[end_line + 2] if end_line + 2 < len(line_starts) else len(source)
        closing = _CLOSING_LITERAL_RE.match(source[closing_start:closing_line_end])
        if closing is None:
            cursor = _skip_ordinary_string(source, string_start)
            continue

        patch_lines = [_line_text(source, line_starts, line) for line in range(first_content_line, end_line + 1)]
        if not _is_valid_apply_patch_program(patch_lines):
            cursor = _skip_ordinary_string(source, string_start)
            continue
        closing_end = closing_start + closing.start("quote") + 3
        literals.append((opening.start(), closing_end, source[opening.start("newline") : closing_start]))
        cursor = closing_end
    return literals


def _is_valid_apply_patch_program(lines: list[str]) -> bool:
    """Validate the narrow Apply Patch grammar before treating text as transport."""

    if len(lines) < 3 or lines[0] != _BEGIN_PATCH or lines[-1] != _END_PATCH:
        return False
    index = 1
    operation_count = 0
    while index < len(lines) - 1:
        line = lines[index]
        if line.startswith("*** Add File: ") and line[len("*** Add File: ") :]:
            operation_count += 1
            index += 1
            while index < len(lines) - 1 and lines[index].startswith("+"):
                index += 1
        elif line.startswith("*** Delete File: ") and line[len("*** Delete File: ") :]:
            operation_count += 1
            index += 1
        elif line.startswith("*** Update File: ") and line[len("*** Update File: ") :]:
            operation_count += 1
            index += 1
            if index < len(lines) - 1 and lines[index].startswith("*** Move to: ") and lines[index][len("*** Move to: ") :]:
                index += 1
            hunk_count = 0
            while index < len(lines) - 1 and lines[index].startswith("@@"):
                hunk_count += 1
                index += 1
                while index < len(lines) - 1 and lines[index][:1] in {" ", "+", "-"}:
                    index += 1
                if index < len(lines) - 1 and lines[index] == "*** End of File":
                    index += 1
            if hunk_count == 0:
                return False
        else:
            return False
    return operation_count > 0


def _first_exact_end_patch_line(source: str, line_starts: tuple[int, ...], first_line: int) -> int | None:
    for line in range(first_line, len(line_starts)):
        if _line_text(source, line_starts, line) == _END_PATCH:
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


def _identity_transform(source: str) -> ApplyPatchTransform:
    return ApplyPatchTransform(
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
