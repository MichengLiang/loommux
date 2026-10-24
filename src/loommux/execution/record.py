"""Record one accepted cell and its observable execution facts."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Literal

import tiktoken

from loommux.execution.events import IMAGE_MIME_PREFERENCE, PresentationElement, PresentationFailure, PresentationImage, PresentationText
from loommux.execution.logs import ExecutionLogs
from loommux.execution.terminal import TerminalTextNormalizer
from loommux.notice import NOTICES_PER_EXECUTION, bounded, more_notices

ExecutionStatus = Literal["running", "completed", "error", "interrupted", "killed"]
OUTPUT_TOKEN_ENCODING = "o200k_base"
OUTPUT_TOKEN_LIMIT = 5_000


@cache
def _output_token_encoding() -> tiktoken.Encoding:
    """Load the one tokenizer used by the private automatic-delivery policy."""
    return tiktoken.get_encoding(OUTPUT_TOKEN_ENCODING)


def _count_output_tokens(text: str) -> int:
    """Count arbitrary visible output as ordinary ``o200k_base`` text."""
    return len(_output_token_encoding().encode_ordinary(text))


@dataclass
class Execution:
    """Store one accepted cell's lifecycle facts and visible output events.

    The record is the durable-in-session address for everything observed from a
    submission. It retains normalized text and rich display events so consumers
    can choose a later projection without asking the kernel to replay history.
    """

    execution: int
    kernel_pid: int
    submitted_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    status: ExecutionStatus = "running"
    # This flag is the sole request-derived state that a later wait() needs.
    # Source and initial wait policy have no runtime consumer after submission.
    _full_output_requested: bool = field(default=False, repr=False)
    error: dict[str, Any] | None = None
    completed_at: float | None = None
    msg_id: str | None = None
    interrupt_requested: bool = False
    done: threading.Event = field(default_factory=threading.Event, repr=False)
    # When an output message last arrived, on the monotonic clock: --silence measures
    # from it. Set before normalization, since a line redrawn in place with \r is
    # activity even when the normalized log does not grow; a notice is not output.
    last_output_monotonic: float = field(default_factory=time.monotonic, repr=False)
    logs: ExecutionLogs = field(default_factory=ExecutionLogs, init=False)
    presentation: list[PresentationElement] = field(default_factory=list, init=False)
    # Notices are not output: no stream and no token limit sees them. They are
    # kept apart until a result delivers them, once.
    notices: list[str] = field(default_factory=list, init=False)
    notices_not_kept: int = field(default=0, init=False)
    notices_delivered: bool = field(default=False, init=False)
    # Kept as parts and joined on read: appending to one string attribute
    # copies all of it each time, which is quadratic for many displays.
    _result_parts: list[str] = field(default_factory=list, init=False, repr=False)
    _next_display_ordinal: int = field(default=1, init=False, repr=False)
    _stdout_normalizer: TerminalTextNormalizer = field(default_factory=TerminalTextNormalizer, init=False, repr=False)
    _stderr_normalizer: TerminalTextNormalizer = field(default_factory=TerminalTextNormalizer, init=False, repr=False)
    _result_normalizer: TerminalTextNormalizer = field(default_factory=TerminalTextNormalizer, init=False, repr=False)
    _traceback_normalizer: TerminalTextNormalizer = field(default_factory=TerminalTextNormalizer, init=False, repr=False)
    _output_token_count: int | None = field(default=None, init=False, repr=False)
    _output_token_count_is_current: bool = field(default=False, init=False, repr=False)

    def append_stdout(self, text: str) -> str:
        self.last_output_monotonic = time.monotonic()
        normalized = self._stdout_normalizer.normalize(text)
        self.logs.append_stdout(normalized)
        if normalized:
            self._invalidate_output_token_count()
        self._append_presentation_text(normalized)
        self.updated_at = time.time()
        return normalized

    def append_stderr(self, text: str) -> str:
        self.last_output_monotonic = time.monotonic()
        normalized = self._stderr_normalizer.normalize(text)
        self.logs.append_stderr(normalized)
        if normalized:
            self._invalidate_output_token_count()
        self._append_presentation_text(normalized)
        self.updated_at = time.time()
        return normalized

    def append_result_text(self, text: str) -> str:
        self.last_output_monotonic = time.monotonic()
        normalized = self._result_normalizer.normalize(text)
        if normalized:
            self._result_parts.append(normalized)
        self.logs.append_result(normalized, self.execution)
        if normalized:
            self._invalidate_output_token_count()
        self._append_presentation_text(normalized)
        self.updated_at = time.time()
        return normalized

    def record_error(self, error: dict[str, Any]) -> str:
        self.last_output_monotonic = time.monotonic()
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
            if output:
                self._invalidate_output_token_count()
            self._append_presentation_text(output)
        self.error = normalized_error
        self.status = "error"
        self.updated_at = time.time()
        return output

    def append_notice(self, text: str) -> None:
        """Keep one notice the cell spoke, within the channel's bounds."""
        if len(self.notices) >= NOTICES_PER_EXECUTION:
            self.notices_not_kept += 1
            return
        # Each notice is whole when it arrives, so a fresh normalizer leaves no
        # control sequence open across notices.
        self.notices.append(bounded(TerminalTextNormalizer().normalize(text)))

    def notice_texts(self) -> list[str]:
        """The kept notices, followed by how many were not kept, if any."""
        return [*self.notices, more_notices(self.notices_not_kept)] if self.notices_not_kept else list(self.notices)

    def append_display_data(self, data: object, metadata: object) -> None:
        """Record one rich display event after its text/plain projection.

        The data bundle is retained as supplied because its Base64 and detail fields
        must be diagnosed at the MCP boundary, where delivery limits are known.
        """
        self.last_output_monotonic = time.monotonic()
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

    def record_kernel_exit(self, returncode: int | None) -> None:
        detail = (
            "kernel process exited"
            if returncode is None
            else f"kernel process exited with status {returncode}"
        )
        self.error = {
            "ename": "KernelProcessExited",
            "evalue": detail,
            "traceback": [],
        }
        self.kill()

    @property
    def is_running(self) -> bool:
        return self.status == "running"

    @property
    def result_text(self) -> str:
        return "\n".join(self._result_parts)

    def snapshot(self) -> dict[str, Any]:
        combined_log = self.logs.combined
        output_total_lines = combined_log.line_count
        omission_reason = self._output_omitted_reason()
        omitted = omission_reason is not None
        result: dict[str, Any] = {
            "ok": self.status not in {"error", "killed"},
            "execution": self.execution,
            "status": self.status,
            "stdout": "" if omitted else self.logs.stdout.text,
            "stderr": "" if omitted else self.logs.stderr.text,
            "result_text": "" if omitted else self.result_text,
            "error": self.error_summary,
            "output_omitted": omitted,
            "output_omitted_reason": omission_reason,
            "output_total_lines": output_total_lines,
            "output_total_characters": combined_log.character_count,
            "output_total_utf8_bytes": combined_log.utf8_byte_count,
        }
        if not omitted:
            result["output_text"] = self.logs.combined.text
        return result

    def status_snapshot(self) -> dict[str, Any]:
        combined_log = self.logs.combined
        output_total_lines = combined_log.line_count
        return {
            "ok": self.status not in {"error", "killed"},
            "execution": self.execution,
            "status": self.status,
            "submitted_at": self.submitted_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "kernel_pid": self.kernel_pid,
            "error": self.error_summary,
            "output_total_lines": output_total_lines,
            "output_total_characters": combined_log.character_count,
            "output_total_utf8_bytes": combined_log.utf8_byte_count,
            "output_omitted_reason": self._output_omitted_reason(),
        }

    @property
    def error_summary(self) -> dict[str, Any] | None:
        if self.error is None:
            return None
        return {key: self.error.get(key) for key in ("ename", "evalue") if key in self.error}

    def _output_omitted_reason(self) -> str | None:
        """Apply the sole automatic-delivery limit to terminal output."""
        if self.status == "running":
            return "running"
        if self._full_output_requested:
            return None
        if self._combined_output_token_count() > OUTPUT_TOKEN_LIMIT:
            return "token_limit_exceeded"
        return None

    def _combined_output_token_count(self) -> int:
        if self._output_token_count_is_current:
            assert self._output_token_count is not None
            return self._output_token_count
        count = _count_output_tokens(self.logs.combined.text)
        self._output_token_count = count
        self._output_token_count_is_current = True
        return count

    def _invalidate_output_token_count(self) -> None:
        self._output_token_count = None
        self._output_token_count_is_current = False
