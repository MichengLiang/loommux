from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal

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

    def append_stdout(self, text: str) -> None:
        self.stdout += text
        self.updated_at = time.time()

    def append_stderr(self, text: str) -> None:
        self.stderr += text
        self.updated_at = time.time()

    def append_result_text(self, text: str) -> None:
        if self.result_text:
            self.result_text += "\n"
        self.result_text += text
        self.updated_at = time.time()

    def record_error(self, error: dict[str, Any]) -> None:
        self.error = error
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

    def snapshot(self) -> dict[str, Any]:
        return {
            "ok": self.status not in {"error", "killed"},
            "execution_id": self.execution_id,
            "status": self.status,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "result_text": self.result_text,
            "error": self.error,
        }
