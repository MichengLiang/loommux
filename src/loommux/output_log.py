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

    def read(self, line_range: str | None = None, *, show_line_numbers: bool = False, max_chars: int | None = None) -> dict[str, object]:
        max_chars_error = _validate_max_chars(max_chars)
        if max_chars_error is not None:
            return max_chars_error
        lines = self._lines()
        range_result = _resolve_line_range(line_range, len(lines))
        if isinstance(range_result, dict):
            return range_result
        selected = _selected_lines(lines, range_result)
        rendered = [_render_line(line, line_number, show_line_numbers=show_line_numbers, max_chars=max_chars) for line_number, line in selected]
        return {
            "ok": True,
            "line_range": line_range,
            "show_line_numbers": show_line_numbers,
            "total_lines": len(lines),
            "returned_lines": len(selected),
            "omitted_before": max(range_result.start - 1, 0) if selected else len(lines),
            "omitted_after": max(len(lines) - range_result.stop, 0) if selected else 0,
            "text": "\n".join(rendered),
        }

    def search(
        self,
        query: str,
        *,
        query_mode: str = "auto",
        context_before: int = 0,
        context_after: int = 0,
        ignore_case: bool = False,
        max_chars: int | None = None,
    ) -> dict[str, object]:
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
        matched: dict[int, int] = {}
        for index, line in enumerate(lines, start=1):
            count = matcher(line)
            if count:
                matched[index] = count

        selected_numbers: set[int] = set()
        for line_number in matched:
            start = max(1, line_number - context_before)
            stop = min(len(lines), line_number + context_after)
            selected_numbers.update(range(start, stop + 1))

        rendered: list[str] = []
        for line_number in sorted(selected_numbers):
            prefix = "M" if line_number in matched else "C"
            line = _clip_line(lines[line_number - 1], max_chars)
            rendered.append(f"{prefix} {line_number} | {line}")

        return {
            "ok": True,
            "query": query,
            "query_interpretation": interpretation,
            "matched_lines": len(matched),
            "matches": sum(matched.values()),
            "context_before": context_before,
            "context_after": context_after,
            "total_lines": len(lines),
            "text": "\n".join(rendered),
        }

    def _lines(self) -> list[str]:
        if not self._text:
            return []
        return self._text.splitlines()


class ExecutionLogs:
    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        self.combined = LineLog()
        self.stdout = LineLog()
        self.stderr = LineLog()
        self.result = LineLog()
        self.traceback = LineLog()

    @property
    def handles(self) -> dict[str, str]:
        return {
            "combined": self.handle("combined"),
            "stdout": self.handle("stdout"),
            "stderr": self.handle("stderr"),
            "result": self.handle("result"),
            "traceback": self.handle("traceback"),
        }

    @property
    def output_log(self) -> str:
        return self.handle("combined")

    def handle(self, stream: str) -> str:
        if stream == "combined":
            return f"python-output:{self.execution_id}"
        return f"python-output:{self.execution_id}/{stream}"

    def get(self, stream: str) -> LineLog | None:
        if stream == "combined":
            return self.combined
        if stream == "stdout":
            return self.stdout
        if stream == "stderr":
            return self.stderr
        if stream == "result":
            return self.result
        if stream == "traceback":
            return self.traceback
        return None

    def append_stdout(self, text: str) -> None:
        self.stdout.append(text)
        self.combined.append(text)

    def append_stderr(self, text: str) -> None:
        self.stderr.append(text)
        self.combined.append(text)

    def append_result(self, text: str, execution_count: int | None) -> None:
        if text and not text.endswith("\n"):
            text_for_result = f"{text}\n"
        else:
            text_for_result = text
        self.result.append(text_for_result)
        label = f"Out[{execution_count}]" if execution_count is not None else "Out"
        combined_text = _prefix_first_line(text, f"{label}: ")
        if combined_text and not combined_text.endswith("\n"):
            combined_text += "\n"
        self.combined.append(combined_text)

    def append_traceback(self, lines: list[str]) -> None:
        text = "\n".join(lines)
        if text and not text.endswith("\n"):
            text += "\n"
        self.traceback.append(text)
        self.combined.append(text)


def parse_output_log_handle(handle: str) -> tuple[str, str] | dict[str, object]:
    prefix = "python-output:"
    if not handle.startswith(prefix):
        return {"ok": False, "status": "invalid_output_log", "message": "output_log must start with python-output:"}
    remainder = handle[len(prefix) :]
    execution_id, separator, stream = remainder.partition("/")
    if not execution_id:
        return {"ok": False, "status": "invalid_output_log", "message": "output_log must include an execution id"}
    if not separator:
        return execution_id, "combined"
    if stream not in {"stdout", "stderr", "result", "traceback"}:
        return {"ok": False, "status": "invalid_output_log", "message": "output_log stream is not supported"}
    return execution_id, stream


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
    start = max(1, min(start, total_lines + 1))
    stop = max(0, min(stop, total_lines))
    return ResolvedLineRange(start, stop)


def _resolve_endpoint(raw: str, total_lines: int, *, default: int) -> int:
    if raw == "":
        return default
    value = int(raw)
    if value < 0:
        return total_lines + value + 1
    return value


def _selected_lines(lines: list[str], line_range: ResolvedLineRange) -> list[tuple[int, str]]:
    if line_range.start > line_range.stop:
        return []
    return [(line_number, lines[line_number - 1]) for line_number in range(line_range.start, line_range.stop + 1)]


def _render_line(line: str, line_number: int, *, show_line_numbers: bool, max_chars: int | None) -> str:
    clipped = _clip_line(line, max_chars)
    if show_line_numbers:
        return f"{line_number} | {clipped}"
    return clipped


def _clip_line(line: str, max_chars: int | None) -> str:
    if max_chars is None or len(line) <= max_chars:
        return line
    omitted = len(line) - max_chars
    return f"{line[:max_chars]}...[{omitted} chars omitted]"


def _validate_max_chars(max_chars: int | None) -> dict[str, object] | None:
    if max_chars is None:
        return None
    if max_chars <= 0:
        return {"ok": False, "status": "invalid_max_chars", "message": "max_chars must be greater than 0"}
    return None


def _compile_matcher(query: str, query_mode: str, ignore_case: bool) -> tuple[Matcher, str] | dict[str, object]:
    flags = re.IGNORECASE if ignore_case else 0
    if query_mode not in {"auto", "literal", "regex"}:
        return {"ok": False, "status": "invalid_query", "message": "query_mode must be auto, literal, or regex"}
    if query_mode == "literal":
        needle = query.lower() if ignore_case else query

        def literal_matcher(line: str) -> int:
            haystack = line.lower() if ignore_case else line
            if needle == "":
                return 1
            return haystack.count(needle)

        return literal_matcher, "literal"
    try:
        pattern = re.compile(query, flags)
    except re.error:
        if query_mode == "regex":
            return {"ok": False, "status": "invalid_query", "message": "query is not a valid regular expression"}
        return _compile_matcher(query, "literal", ignore_case)

    def regex_matcher(line: str) -> int:
        matches = pattern.findall(line)
        return len(matches)

    return regex_matcher, "regex"


def _prefix_first_line(text: str, prefix: str) -> str:
    if not text:
        return prefix.rstrip()
    lines = text.splitlines(keepends=True)
    if not lines:
        return prefix.rstrip()
    lines[0] = f"{prefix}{lines[0]}"
    return "".join(lines)
