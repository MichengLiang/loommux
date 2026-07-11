from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from loommux.output_log import ExecutionLogs

ExecutionStatus = Literal["running", "completed", "error", "interrupted", "killed"]
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


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

    def append_stdout(self, text: str) -> None:
        self.stdout += text
        self.logs.append_stdout(text)
        self.updated_at = time.time()

    def append_stderr(self, text: str) -> None:
        self.stderr += text
        self.logs.append_stderr(text)
        self.updated_at = time.time()

    def append_result_text(self, text: str) -> None:
        if self.result_text:
            self.result_text += "\n"
        self.result_text += text
        self.logs.append_result(text, self.execution)
        self.updated_at = time.time()

    def record_error(self, error: dict[str, Any]) -> None:
        self.error = error
        traceback = error.get("traceback")
        if isinstance(traceback, list):
            self.logs.append_traceback([_strip_ansi(str(line)) for line in traceback])
        self.status = "error"
        self.updated_at = time.time()

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


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)
