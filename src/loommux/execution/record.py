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

ExecutionStatus = Literal["running", "completed", "error", "interrupted", "killed"]
OUTPUT_TOKEN_ENCODING = "o200k_base"


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
    _output_token_count: int | None = field(default=None, init=False, repr=False)
    _output_token_count_is_current: bool = field(default=False, init=False, repr=False)

    def append_stdout(self, text: str) -> str:
        normalized = self._stdout_normalizer.normalize(text)
        self.stdout += normalized
        self.logs.append_stdout(normalized)
        if normalized:
            self._invalidate_output_token_count()
        self._append_presentation_text(normalized)
        self.updated_at = time.time()
        return normalized

    def append_stderr(self, text: str) -> str:
        normalized = self._stderr_normalizer.normalize(text)
        self.stderr += normalized
        self.logs.append_stderr(normalized)
        if normalized:
            self._invalidate_output_token_count()
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
        if normalized:
            self._invalidate_output_token_count()
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
            if output:
                self._invalidate_output_token_count()
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

    def snapshot(self, output_line_limit: int | None = None, output_token_bypass_limit: int | None = None) -> dict[str, Any]:
        combined_log = self.logs.combined
        output_total_lines = combined_log.line_count
        omission_reason = self._output_omitted_reason(output_line_limit, output_token_bypass_limit, output_total_lines)
        omitted = omission_reason is not None
        result: dict[str, Any] = {
            "ok": self.status not in {"error", "killed"},
            "execution": self.execution,
            "status": self.status,
            "stdout": "" if omitted else self.stdout,
            "stderr": "" if omitted else self.stderr,
            "result_text": "" if omitted else self.result_text,
            "error": self._error_summary(),
            "output_omitted": omitted,
            "output_omitted_reason": omission_reason,
            "output_line_limit": output_line_limit,
            "output_total_lines": output_total_lines,
            "output_total_characters": combined_log.character_count,
            "output_total_utf8_bytes": combined_log.utf8_byte_count,
        }
        if not omitted:
            result["output_text"] = self.logs.combined.text
        return result

    def status_snapshot(self, output_line_limit: int | None = None, output_token_bypass_limit: int | None = None) -> dict[str, Any]:
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
            "execution_count_at_submit": self.execution_count_at_submit,
            "error": self._error_summary(),
            "output_total_lines": output_total_lines,
            "output_total_characters": combined_log.character_count,
            "output_total_utf8_bytes": combined_log.utf8_byte_count,
            "output_omitted_reason": self._output_omitted_reason(output_line_limit, output_token_bypass_limit, output_total_lines),
        }

    def _error_summary(self) -> dict[str, Any] | None:
        if self.error is None:
            return None
        return {key: self.error.get(key) for key in ("ename", "evalue") if key in self.error}

    def _output_omitted_reason(self, output_line_limit: int | None, output_token_bypass_limit: int | None, output_total_lines: int) -> str | None:
        """Apply the public line limit only after the private token exemption.

        The token threshold is intentionally not projected into response fields or
        MCP descriptions. It only proves that a many-line combined log is still
        compact enough for automatic delivery. If the tokenizer is unavailable,
        the established 300-line policy remains the conservative fallback.
        """
        if self.status == "running":
            return "running"
        if self._full_output_requested:
            return None
        if output_token_bypass_limit is not None:
            combined_log = self.logs.combined
            # Ordinary BPE tokens each represent at least one UTF-8 byte. This
            # exact shortcut avoids loading the encoding table for common small
            # outputs while preserving the same token-threshold decision.
            if combined_log.utf8_byte_count <= output_token_bypass_limit:
                return None
            output_token_count = self._combined_output_token_count()
            if output_token_count is not None and output_token_count <= output_token_bypass_limit:
                return None
        if output_line_limit is not None and output_total_lines > output_line_limit:
            return "line_limit_exceeded"
        return None

    def _combined_output_token_count(self) -> int | None:
        if self._output_token_count_is_current:
            return self._output_token_count
        try:
            count = _count_output_tokens(self.logs.combined.text)
        except (ImportError, OSError, RuntimeError, ValueError):
            count = None
        self._output_token_count = count
        self._output_token_count_is_current = True
        return count

    def _invalidate_output_token_count(self) -> None:
        self._output_token_count = None
        self._output_token_count_is_current = False
