from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

from loommux.output_log import ExecutionLogs
from loommux.terminal_text import TerminalTextNormalizer

ExecutionStatus = Literal["running", "completed", "error", "interrupted", "killed"]


@dataclass(frozen=True)
class PresentationText:
    """A normalized visible text fragment at its IOPub arrival position."""

    text: str


@dataclass(frozen=True)
class PresentationImage:
    """One display-data image before MCP transport validation."""

    data: object
    mime_type: str
    detail: object
    display_ordinal: int


@dataclass(frozen=True)
class PresentationFailure:
    """A visible diagnostic occupying the position of an undeliverable image."""

    message: str


PresentationElement: TypeAlias = PresentationText | PresentationImage | PresentationFailure
IMAGE_MIME_PREFERENCE = ("image/png", "image/jpeg", "image/webp", "image/gif")


@dataclass
class Execution:
    """One accepted cell, addressed for the lifetime of this server process."""

    execution: int
    code: str
    kernel_pid: int
    submitted_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    status: ExecutionStatus = "running"
    full_output_requested: bool = False
    stdout: str = ""
    stderr: str = ""
    result_text: str = ""
    error: dict[str, Any] | None = None
    completed_at: float | None = None
    execution_count_at_submit: int | None = None
    msg_id: str | None = None
    interrupt_requested: bool = False
    done: threading.Event = field(default_factory=threading.Event, repr=False)
    logs: ExecutionLogs = field(default_factory=ExecutionLogs, init=False)
    presentation: list[PresentationElement] = field(default_factory=list, init=False)
    _next_display_ordinal: int = field(default=1, init=False, repr=False)
    _stdout_normalizer: TerminalTextNormalizer = field(default_factory=TerminalTextNormalizer, init=False, repr=False)
    _stderr_normalizer: TerminalTextNormalizer = field(default_factory=TerminalTextNormalizer, init=False, repr=False)
    _result_normalizer: TerminalTextNormalizer = field(default_factory=TerminalTextNormalizer, init=False, repr=False)
    _traceback_normalizer: TerminalTextNormalizer = field(default_factory=TerminalTextNormalizer, init=False, repr=False)

    def append_stdout(self, text: str) -> str:
        normalized = self._stdout_normalizer.normalize(text)
        self.stdout += normalized
        self.logs.append_stdout(normalized)
        self._append_presentation_text(normalized)
        self.updated_at = time.time()
        return normalized

    def append_stderr(self, text: str) -> str:
        normalized = self._stderr_normalizer.normalize(text)
        self.stderr += normalized
        self.logs.append_stderr(normalized)
        self._append_presentation_text(normalized)
        self.updated_at = time.time()
        return normalized

    def append_result_text(self, text: str) -> str:
        normalized = self._result_normalizer.normalize(text)
        if normalized and self.result_text:
            self.result_text += "\n"
        if normalized:
            self.result_text += normalized
        self.logs.append_result(normalized, self.execution)
        self._append_presentation_text(normalized)
        self.updated_at = time.time()
        return normalized

    def record_error(self, error: dict[str, Any]) -> str:
        normalized_error = dict(error)
        for key in ("ename", "evalue"):
            if key in normalized_error:
                normalized_error[key] = self._traceback_normalizer.normalize(str(normalized_error[key]))
        traceback = normalized_error.get("traceback")
        output = ""
        if isinstance(traceback, list):
            normalized_traceback = [self._traceback_normalizer.normalize(str(line)) for line in traceback]
            normalized_error["traceback"] = normalized_traceback
            output = self.logs.append_traceback(normalized_traceback)
            self._append_presentation_text(output)
        self.error = normalized_error
        self.status = "error"
        self.updated_at = time.time()
        return output

    def append_display_data(self, data: object, metadata: object) -> None:
        """Record one rich display event after its text/plain projection.

        The data bundle is retained as supplied because its Base64 and detail fields
        must be diagnosed at the MCP boundary, where delivery limits are known.
        """
        ordinal = self._next_display_ordinal
        self._next_display_ordinal += 1
        if not isinstance(data, dict):
            return

        for mime_type in IMAGE_MIME_PREFERENCE:
            if mime_type in data:
                detail = metadata.get("detail") if isinstance(metadata, dict) else None
                self.presentation.append(
                    PresentationImage(data[mime_type], mime_type, detail, ordinal)
                )
                return

        unsupported = next(
            (
                mime_type
                for mime_type in data
                if isinstance(mime_type, str) and mime_type.startswith("image/")
            ),
            None,
        )
        if unsupported is not None:
            self.presentation.append(
                PresentationFailure(
                    f"Image delivery failed for execution {self.execution} display "
                    f"{ordinal}: unsupported MIME type {unsupported}."
                )
            )

    @property
    def has_rich_presentation(self) -> bool:
        return any(not isinstance(element, PresentationText) for element in self.presentation)

    def _append_presentation_text(self, text: str) -> None:
        if text:
            self.presentation.append(PresentationText(text))

    def finish(self) -> None:
        if self.status == "running":
            self.status = "completed"
        if self.status == "error" and self.interrupt_requested and self.error and self.error.get("ename") == "KeyboardInterrupt":
            self.status = "interrupted"
        self.completed_at = time.time()
        self.updated_at = self.completed_at
        self.done.set()

    def kill(self) -> None:
        self.status = "killed"
        self.completed_at = time.time()
        self.updated_at = self.completed_at
        self.done.set()

    @property
    def is_running(self) -> bool:
        return self.status == "running"

    def snapshot(self, output_line_limit: int | None = None) -> dict[str, Any]:
        output_total_lines = self.logs.combined.line_count
        omission_reason = self._output_omitted_reason(output_line_limit, output_total_lines)
        omitted = omission_reason is not None
        result: dict[str, Any] = {
            "ok": self.status not in {"error", "killed"},
            "execution": self.execution,
            "status": self.status,
            "full_output_requested": self.full_output_requested,
            "stdout": "" if omitted else self.stdout,
            "stderr": "" if omitted else self.stderr,
            "result_text": "" if omitted else self.result_text,
            "error": self._error_summary(),
            "output_omitted": omitted,
            "output_omitted_reason": omission_reason,
            "output_line_limit": output_line_limit,
            "output_total_lines": output_total_lines,
        }
        if not omitted:
            result["output_text"] = self.logs.combined.text
        return result

    def status_snapshot(self, output_line_limit: int | None = None) -> dict[str, Any]:
        output_total_lines = self.logs.combined.line_count
        return {
            "ok": self.status not in {"error", "killed"},
            "execution": self.execution,
            "status": self.status,
            "full_output_requested": self.full_output_requested,
            "submitted_at": self.submitted_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "kernel_pid": self.kernel_pid,
            "execution_count_at_submit": self.execution_count_at_submit,
            "error": self._error_summary(),
            "output_total_lines": output_total_lines,
            "output_omitted_reason": self._output_omitted_reason(output_line_limit, output_total_lines),
        }

    def _error_summary(self) -> dict[str, Any] | None:
        if self.error is None:
            return None
        return {key: self.error.get(key) for key in ("ename", "evalue") if key in self.error}

    def _output_omitted_reason(self, output_line_limit: int | None, output_total_lines: int) -> str | None:
        if self.status == "running":
            return "running"
        if self.full_output_requested:
            return None
        if output_line_limit is not None and output_total_lines > output_line_limit:
            return "line_limit_exceeded"
        return None
