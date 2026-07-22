from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

QueryMode = Literal["auto", "literal", "regex"]
Matcher = Callable[[str], int]


@dataclass(frozen=True)
class ResolvedLineRange:
    start: int
    stop: int


class LineLog:
    def __init__(self) -> None:
        self._text = ""

    @property
    def text(self) -> str:
        return self._text

    @property
    def line_count(self) -> int:
        return len(self._lines())

    def append(self, text: str) -> None:
        self._text += text

    def read(self, line_range: str | None = None, *, max_chars: int | None = None) -> dict[str, object]:
        max_chars_error = _validate_max_chars(max_chars)
        if max_chars_error is not None:
            return max_chars_error
        lines = self._lines()
        resolved = _resolve_line_range(line_range, len(lines))
        if isinstance(resolved, dict):
            return resolved
        selected = _selected_lines(lines, resolved)
        return {
            "ok": True,
            "line_range": line_range,
            "total_lines": len(lines),
            "returned_lines": len(selected),
            "omitted_before": max(resolved.start - 1, 0) if selected else len(lines),
            "omitted_after": max(len(lines) - resolved.stop, 0) if selected else 0,
            "text": "\n".join(_clip_line(line, max_chars) for line in selected),
        }

    def search(self, query: str, *, query_mode: str = "auto", context_before: int = 0, context_after: int = 0, ignore_case: bool = False, max_chars: int | None = None) -> dict[str, object]:
        max_chars_error = _validate_max_chars(max_chars)
        if max_chars_error is not None:
            return max_chars_error
        if context_before < 0 or context_after < 0:
            return {"ok": False, "status": "invalid_context", "message": "context values must be greater than or equal to 0"}
        matcher_result = _compile_matcher(query, query_mode, ignore_case)
        if isinstance(matcher_result, dict):
            return matcher_result
        matcher, interpretation = matcher_result
        lines = self._lines()
        matched = {number: count for number, line in enumerate(lines, start=1) if (count := matcher(line))}
        selected_numbers = {number for match in matched for number in range(max(1, match - context_before), min(len(lines), match + context_after) + 1)}
        rendered = [f"{'M' if number in matched else 'C'} {number} | {_clip_line(lines[number - 1], max_chars)}" for number in sorted(selected_numbers)]
        return {"ok": True, "query": query, "query_interpretation": interpretation, "matched_lines": len(matched), "matches": sum(matched.values()), "context_before": context_before, "context_after": context_after, "total_lines": len(lines), "text": "\n".join(rendered)}

    def _lines(self) -> list[str]:
        return self._text.splitlines() if self._text else []


class ExecutionLogs:
    """In-memory stream projections for one execution; they have no public address."""

    def __init__(self) -> None:
        self.combined = LineLog()
        self.stdout = LineLog()
        self.stderr = LineLog()
        self.result = LineLog()
        self.traceback = LineLog()

    def get(self, stream: str) -> LineLog | None:
        return getattr(self, stream, None) if stream in {"combined", "stdout", "stderr", "result", "traceback"} else None

    def append_stdout(self, text: str) -> None:
        self.stdout.append(text)
        self.combined.append(text)

    def append_stderr(self, text: str) -> None:
        self.stderr.append(text)
        self.combined.append(text)

    def append_result(self, text: str, execution: int) -> None:
        if not text:
            return
        value = text if text.endswith("\n") else f"{text}\n"
        self.result.append(value)
        self.combined.append(_prefix_first_line(value, f"Out[{execution}]: "))

    def append_traceback(self, lines: list[str]) -> str:
        value = "\n".join(lines)
        value = value if not value or value.endswith("\n") else f"{value}\n"
        self.traceback.append(value)
        self.combined.append(value)
        return value


def _resolve_line_range(line_range: str | None, total_lines: int) -> ResolvedLineRange | dict[str, object]:
    if total_lines == 0:
        return ResolvedLineRange(1, 0)
    if line_range is None:
        return ResolvedLineRange(1, total_lines)
    if ":" not in line_range:
        return {"ok": False, "status": "invalid_line_range", "message": "line_range must use start:stop"}
    start_raw, stop_raw = line_range.split(":", 1)
    try:
        start = _resolve_endpoint(start_raw, total_lines, default=1)
        stop = _resolve_endpoint(stop_raw, total_lines, default=total_lines)
    except ValueError:
        return {"ok": False, "status": "invalid_line_range", "message": "line_range endpoints must be integers"}
    return ResolvedLineRange(max(1, min(start, total_lines + 1)), max(0, min(stop, total_lines)))


def _resolve_endpoint(raw: str, total_lines: int, *, default: int) -> int:
    if raw == "":
        return default
    value = int(raw)
    return total_lines + value + 1 if value < 0 else value


def _selected_lines(lines: list[str], line_range: ResolvedLineRange) -> list[str]:
    return [] if line_range.start > line_range.stop else lines[line_range.start - 1 : line_range.stop]


def _clip_line(line: str, max_chars: int | None) -> str:
    if max_chars is None or len(line) <= max_chars:
        return line
    return f"{line[:max_chars]}...[{len(line) - max_chars} chars omitted]"


def _validate_max_chars(max_chars: int | None) -> dict[str, object] | None:
    return None if max_chars is None or max_chars > 0 else {"ok": False, "status": "invalid_max_chars", "message": "max_chars must be greater than 0"}


def _compile_matcher(query: str, query_mode: str, ignore_case: bool) -> tuple[Matcher, str] | dict[str, object]:
    if query_mode not in {"auto", "literal", "regex"}:
        return {"ok": False, "status": "invalid_query", "message": "query_mode must be auto, literal, or regex"}
    flags = re.IGNORECASE if ignore_case else 0
    if query_mode == "literal":
        needle = query.lower() if ignore_case else query
        return (lambda line: (line.lower() if ignore_case else line).count(needle) if needle else 1), "literal"
    try:
        pattern = re.compile(query, flags)
    except re.error:
        return {"ok": False, "status": "invalid_query", "message": "query is not a valid regular expression"} if query_mode == "regex" else _compile_matcher(query, "literal", ignore_case)
    return lambda line: len(pattern.findall(line)), "regex"


def _prefix_first_line(text: str, prefix: str) -> str:
    lines = text.splitlines(keepends=True)
    return prefix.rstrip() if not lines else f"{prefix}{lines[0]}{''.join(lines[1:])}"
