from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from loommux.output_log import ExecutionLogs

ExecutionStatus = Literal["running", "completed", "error", "interrupted", "killed"]


@dataclass
class Execution:
    execution_id: str
    code: str
    kernel_pid: int
    submitted_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    status: ExecutionStatus = "running"
    stdout: str = ""
    stderr: str = ""
    result_text: str = ""
    error: dict[str, Any] | None = None
    completed_at: float | None = None
    execution_count_at_submit: int | None = None
    msg_id: str | None = None
    interrupt_requested: bool = False
    done: threading.Event = field(default_factory=threading.Event, repr=False)
    logs: ExecutionLogs = field(init=False)

    def __post_init__(self) -> None:
        self.logs = ExecutionLogs(self.execution_id)

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
        self.logs.append_result(text, self.execution_count_at_submit)
        self.updated_at = time.time()

    def record_error(self, error: dict[str, Any]) -> None:
        self.error = error
        traceback = error.get("traceback")
        if isinstance(traceback, list):
            self.logs.append_traceback([str(line) for line in traceback])
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
        output_omitted_reason = self._output_omitted_reason(output_line_limit, output_total_lines)
        output_omitted = output_omitted_reason is not None
        return {
            "ok": self.status not in {"error", "killed"},
            "execution_id": self.execution_id,
            "status": self.status,
            "stdout": "" if output_omitted else self.stdout,
            "stderr": "" if output_omitted else self.stderr,
            "result_text": "" if output_omitted else self.result_text,
            "error": self._status_error(),
            "output_log": self.logs.output_log,
            "logs": self.logs.handles,
            "output_omitted": output_omitted,
            "output_omitted_reason": output_omitted_reason,
            "output_line_limit": output_line_limit,
            "output_total_lines": output_total_lines,
        }

    def status_snapshot(self) -> dict[str, Any]:
        return {
            "ok": self.status not in {"error", "killed"},
            "execution_id": self.execution_id,
            "status": self.status,
            "submitted_at": self.submitted_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "kernel_pid": self.kernel_pid,
            "execution_count_at_submit": self.execution_count_at_submit,
            "error": self._status_error(),
            "output_log": self.logs.output_log,
            "logs": self.logs.handles,
        }

    def _status_error(self) -> dict[str, Any] | None:
        if self.error is None:
            return None
        status_error = {key: self.error.get(key) for key in ("ename", "evalue") if key in self.error}
        status_error["traceback_log"] = self.logs.handle("traceback")
        return status_error

    def _output_omitted_reason(self, output_line_limit: int | None, output_total_lines: int) -> str | None:
        if self.status == "running":
            return "running"
        if output_line_limit is not None and output_total_lines > output_line_limit:
            return "line_limit_exceeded"
        return None
