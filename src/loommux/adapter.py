from __future__ import annotations

import math
import re
import sys
import threading
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

from loommux.execution import Execution
from loommux.kernel_session import KernelSession
from loommux.monitoring import MonitorPublisher, NoopMonitorPublisher, safe_publish
from loommux.source_transform import prepare_protected_multiline_strings

DEFAULT_OUTPUT_LINE_LIMIT = 300
DEFAULT_RUN_PYTHON_TIMEOUT_SECONDS = 10.0
KERNEL_START_ATTEMPTS = 2
OUTPUT_STREAMS = {"combined", "stdout", "stderr", "result", "traceback"}
RUN_PYTHON_TIMEOUT_DIRECTIVE_RE = re.compile(r"^# loommux: timeout_seconds=([1-9][0-9]*|[0-9]+\.[0-9]+)$")
RUN_PYTHON_FULL_OUTPUT_DIRECTIVE_RE = re.compile(r"^# loommux: full_output$")


def parse_run_python_freeform_timeout(freeform: str, protected_line_numbers: frozenset[int] = frozenset()) -> tuple[float, str]:
    matches = [
        float(match.group(1))
        for line_number, line in enumerate(freeform.splitlines())
        if line_number not in protected_line_numbers
        and (match := RUN_PYTHON_TIMEOUT_DIRECTIVE_RE.fullmatch(line))
        and math.isfinite(float(match.group(1)))
        and float(match.group(1)) > 0
    ]
    return (matches[0], "directive") if len(matches) == 1 else (DEFAULT_RUN_PYTHON_TIMEOUT_SECONDS, "default")


def parse_run_python_full_output(freeform: str, protected_line_numbers: frozenset[int] = frozenset()) -> bool:
    return any(line_number not in protected_line_numbers and RUN_PYTHON_FULL_OUTPUT_DIRECTIVE_RE.fullmatch(line) for line_number, line in enumerate(freeform.splitlines()))


class IPythonMCPAdapter:
    """Owns the server-local execution sequence and one persistent kernel."""

    def __init__(self, monitor_publisher: MonitorPublisher | None = None) -> None:
        self.workspace: Path | None = None
        self.workspace_resolution: str | None = None
        self.python_path: Path | None = None
        self.kernel: KernelSession | None = None
        self.monitor_publisher = monitor_publisher or NoopMonitorPublisher()
        self.executions: dict[int, Execution] = {}
        self.current_execution: int | None = None
        self.recent_execution: int | None = None
        self._next_execution = 1
        self._active_call_id: ContextVar[str | None] = ContextVar("loommux_monitor_call_id", default=None)
        self._pending_execution_input: ContextVar[tuple[str, dict[str, Any]] | None] = ContextVar("loommux_pending_execution_input", default=None)
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            kernel, self.kernel = self.kernel, None
            self.current_execution = None
        if kernel is not None:
            kernel.shutdown()
        self.monitor_publisher.close()

    def start_workspace(self, workspace: Path, workspace_resolution: str) -> dict[str, Any]:
        """Start the configured kernel at server lifespan start."""
        workspace = workspace.resolve(strict=False)
        python_path = Path(sys.executable).absolute()
        self._close_kernel()
        if not workspace.exists():
            return self._workspace_error("workspace_not_found", "workspace does not exist", workspace, python_path)
        if not workspace.is_dir():
            return self._workspace_error("workspace_not_directory", "workspace path is not a directory", workspace, python_path)
        kernel: KernelSession | None = None
        start_error: Exception | None = None
        # A kernel can miss its first readiness handshake while its ZMQ channels
        # initialize. Retry once with a fresh process rather than rejecting a
        # valid workspace because of that transient startup race.
        for _attempt in range(KERNEL_START_ATTEMPTS):
            candidate = self._new_kernel_session(workspace, python_path)
            try:
                candidate.start()
            except Exception as exc:
                start_error = exc
                # A retry must never retain the failed session's private root.
                candidate.shutdown(mark_execution_killed=False)
            else:
                kernel = candidate
                break
        if kernel is None:
            if isinstance(start_error, TimeoutError):
                return self._workspace_error("kernel_start_timeout", "kernel did not become ready before timeout", workspace, python_path)
            return self._workspace_error("kernel_start_failed", f"kernel failed to start: {start_error}", workspace, python_path)
        with self._lock:
            self.workspace, self.workspace_resolution, self.python_path, self.kernel = workspace, workspace_resolution, python_path, kernel
            # A server lifespan has one session. This path is startup only; reset_python
            # deliberately does not pass here so it cannot discard existing records.
            self.executions.clear()
            self.current_execution = None
            self.recent_execution = None
            self._next_execution = 1
        return self.python_status()

    def run_python(self, freeform: str) -> dict[str, Any]:
        if not isinstance(freeform, str):
            return {"ok": False, "status": "invalid_code", "message": "freeform must be a string"}
        protection_transform = prepare_protected_multiline_strings(freeform)
        timeout_seconds, _source = parse_run_python_freeform_timeout(freeform, protection_transform.protected_line_numbers)
        token = self._pending_execution_input.set((freeform, protection_transform.as_dict()))
        try:
            return self._submit_python_cell(
                protection_transform.submitted_source,
                timeout_seconds,
                parse_run_python_full_output(freeform, protection_transform.protected_line_numbers),
            )
        finally:
            self._pending_execution_input.reset(token)

    def _submit_python_cell(self, code: str, timeout_seconds: float, full_output_requested: bool = False) -> dict[str, Any]:
        if not isinstance(code, str):
            return {"ok": False, "status": "invalid_code", "message": "code must be a string"}
        if (error := self._validate_timeout(timeout_seconds)) is not None:
            return error
        with self._lock:
            if self.workspace is None:
                return {"ok": False, "status": "workspace_not_set", "message": "workspace has not been set"}
            kernel = self.kernel
            if kernel is None or not kernel.is_alive():
                return {"ok": False, "status": "kernel_not_started", "message": "kernel is not started"}
            if self.current_execution is not None:
                return {"ok": False, "status": "busy", "execution": self.current_execution, "message": "kernel is already executing code"}
            author_source, protection_transform = self._pending_execution_input.get() or (code, None)
            execution = Execution(
                execution=self._next_execution,
                code=code,
                kernel_pid=kernel.pid or 0,
                full_output_requested=full_output_requested,
                author_source=author_source,
                submitted_source=code,
                protection_transform=protection_transform,
            )
            self._next_execution += 1
            self.executions[execution.execution] = execution
            self.current_execution = execution.execution
            self.recent_execution = execution.execution
            self._publish_execution_submitted(execution, timeout_seconds)
        try:
            kernel.submit(execution)
        except Exception as exc:
            with self._lock:
                self.current_execution = None
                execution.record_error({"ename": type(exc).__name__, "evalue": str(exc), "traceback": []})
                execution.finish()
                self._publish_execution_finished(execution)
            return self._execution_response(execution)
        execution.done.wait(float(timeout_seconds))
        return self._execution_response(execution)

    def python_status(self) -> dict[str, Any]:
        with self._lock:
            kernel = self.kernel if self.kernel is not None and self.kernel.is_alive() else None
            if kernel is None:
                self.kernel = None
                self.current_execution = None
            return {
                "ok": True,
                "workspace": str(self.workspace) if self.workspace is not None else None,
                "workspace_resolution": self.workspace_resolution,
                "python": str(self.python_path) if self.python_path is not None else None,
                "kernel_started": kernel is not None,
                "kernel_pid": kernel.pid if kernel is not None else None,
                "busy": self.current_execution is not None,
                "current_execution": self.current_execution,
                "recent_execution": self.recent_execution,
                "kernel_execution_count": kernel.latest_execution_count if kernel is not None else 0,
            }

    def python_execution_status(self, execution: int | None = None) -> dict[str, Any]:
        record = self._select_execution(execution)
        return {"ok": False, "status": "execution_not_found", "message": "execution was not found"} if record is None else self._status_response(record)

    def read_python_output(self, execution: int | None = None, stream: str = "combined", line_range: str | None = None, show_line_numbers: bool = False, max_chars: int | None = None) -> dict[str, Any]:
        record, error = self._select_stream(execution, stream)
        if error is not None:
            return error
        assert record is not None
        line_log = record.logs.get(stream)
        assert line_log is not None
        result = line_log.read(line_range, show_line_numbers=show_line_numbers, max_chars=max_chars)
        if result.get("ok") is not False:
            result.update({"execution": record.execution, "stream": stream})
        return result

    def search_python_output(self, query: str, execution: int | None = None, stream: str = "combined", query_mode: str = "auto", context_before: int = 0, context_after: int = 0, ignore_case: bool = False, max_chars: int | None = None) -> dict[str, Any]:
        record, error = self._select_stream(execution, stream)
        if error is not None:
            return error
        assert record is not None
        line_log = record.logs.get(stream)
        assert line_log is not None
        result = line_log.search(query, query_mode=query_mode, context_before=context_before, context_after=context_after, ignore_case=ignore_case, max_chars=max_chars)
        if result.get("ok") is not False:
            result.update({"execution": record.execution, "stream": stream})
        return result

    def wait_python(self, execution: int | None = None, timeout_seconds: float = 30) -> dict[str, Any]:
        if (error := self._validate_timeout(timeout_seconds)) is not None:
            return error
        record = self._select_execution(execution)
        if record is None:
            return {"ok": False, "status": "execution_not_found", "message": "execution was not found", "kernel": self._kernel_status()}
        if record.is_running:
            record.done.wait(float(timeout_seconds))
        return self._execution_response(record)

    def interrupt_python(self) -> dict[str, Any]:
        with self._lock:
            kernel = self.kernel
            if kernel is None or not kernel.is_alive():
                return {"ok": False, "status": "kernel_not_started", "message": "kernel is not started"}
            if self.current_execution is None:
                return {"ok": True, "status": "idle", "kernel_pid": kernel.pid}
            record = self.executions[self.current_execution]
            record.interrupt_requested = True
        if sys.platform == "win32":
            return self._restart_after_windows_interrupt(kernel, record)
        kernel.interrupt()
        return {"ok": True, "status": "interrupt_sent", "execution": record.execution, "kernel_pid": kernel.pid}

    def _restart_after_windows_interrupt(self, old_kernel: KernelSession, record: Execution) -> dict[str, Any]:
        """Replace a Windows kernel because ipykernel cannot receive Ctrl+C there."""
        with self._lock:
            if self.workspace is None or self.python_path is None or self.kernel is not old_kernel:
                return {"ok": False, "status": "kernel_not_started", "message": "kernel is not started"}
            self.kernel = None
            self.current_execution = None
            record.record_error({"ename": "KeyboardInterrupt", "evalue": "", "traceback": []})
            record.finish()
            self._publish_execution_finished(record)
            workspace, python_path = self.workspace, self.python_path
        old_kernel.shutdown(mark_execution_killed=False)
        kernel = self._new_kernel_session(workspace, python_path)
        try:
            kernel.start()
        except Exception as exc:
            kernel.shutdown(mark_execution_killed=False)
            return self._workspace_error("kernel_start_failed", f"kernel failed to restart after interrupt: {exc}", workspace, python_path)
        with self._lock:
            self.kernel = kernel
        return {"ok": True, "status": "interrupt_sent", "execution": record.execution, "kernel_pid": kernel.pid}

    def reset_python(self) -> dict[str, Any]:
        with self._lock:
            if self.workspace is None or self.python_path is None:
                return {"ok": False, "status": "workspace_not_set", "message": "workspace has not been set"}
            workspace, python_path, old_kernel = self.workspace, self.python_path, self.kernel
            running = self.executions.get(self.current_execution) if self.current_execution is not None else None
            self.kernel = None
            self.current_execution = None
            if running is not None and running.is_running:
                running.kill()
                self._publish_execution_finished(running)
        if old_kernel is not None:
            old_kernel.shutdown(mark_execution_killed=False)
        kernel = self._new_kernel_session(workspace, python_path)
        try:
            kernel.start()
        except Exception as exc:
            kernel.shutdown(mark_execution_killed=False)
            return self._workspace_error("kernel_start_failed", f"kernel failed to restart: {exc}", workspace, python_path)
        with self._lock:
            self.kernel = kernel
        return {"ok": True, "status": "restarted", "workspace": str(workspace), "workspace_resolution": self.workspace_resolution, "python": str(python_path), "kernel_started": True, "kernel_pid": kernel.pid, "busy": False, "current_execution": None, "recent_execution": self.recent_execution}

    def set_active_call_id(self, call_id: str | None) -> Token[str | None]:
        return self._active_call_id.set(call_id)

    def reset_active_call_id(self, token: Token[str |None]) -> None:
        self._active_call_id.reset(token)

    def _close_kernel(self) -> None:
        with self._lock:
            old_kernel, self.kernel = self.kernel, None
            self.current_execution = None
        if old_kernel is not None:
            old_kernel.shutdown()

    def _on_execution_idle(self, record: Execution) -> None:
        with self._lock:
            if self.current_execution == record.execution:
                self.current_execution = None

    def _select_execution(self, execution: int | None) -> Execution | None:
        with self._lock:
            if execution is not None and (isinstance(execution, bool) or not isinstance(execution, int) or execution <= 0):
                return None
            selected = execution if execution is not None else self.current_execution or self.recent_execution
            return self.executions.get(selected) if selected is not None else None

    def _select_stream(self, execution: int | None, stream: str) -> tuple[Execution | None, dict[str, Any] | None]:
        if stream not in OUTPUT_STREAMS:
            return None, {"ok": False, "status": "invalid_stream", "message": "stream must be combined, stdout, stderr, result, or traceback"}
        record = self._select_execution(execution)
        return (None, {"ok": False, "status": "execution_not_found", "message": "execution was not found"}) if record is None else (record, None)

    def _execution_response(self, record: Execution) -> dict[str, Any]:
        result = record.snapshot(output_line_limit=DEFAULT_OUTPUT_LINE_LIMIT)
        if not record.is_running and record.has_rich_presentation:
            # This is consumed by mcp_result_policy only. Keeping it private prevents
            # Base64 payloads from leaking into status JSON or text logs.
            result["_presentation"] = tuple(record.presentation)
        result["kernel"] = self._kernel_status()
        return result

    def _status_response(self, record: Execution) -> dict[str, Any]:
        result = record.status_snapshot(output_line_limit=DEFAULT_OUTPUT_LINE_LIMIT)
        result["kernel"] = self._kernel_status()
        return result

    def _kernel_status(self) -> dict[str, Any]:
        kernel = self.kernel
        return {"busy": self.current_execution is not None, "kernel_pid": kernel.pid if kernel is not None else None, "execution_count": kernel.latest_execution_count if kernel is not None else 0}

    def _new_kernel_session(self, workspace: Path, python_path: Path) -> KernelSession:
        kernel = KernelSession(workspace, python_path, self._on_execution_idle)
        kernel.set_monitor_callbacks(self._publish_execution_output, self._publish_execution_finished)
        return kernel

    def _publish_execution_submitted(self, record: Execution, timeout_seconds: float) -> None:
        safe_publish(
            self.monitor_publisher,
            {
                "type": "execution_submitted",
                "execution": record.execution,
                "call_id": self._active_call_id.get(),
                "workspace": str(self.workspace) if self.workspace else None,
                "kernel_pid": record.kernel_pid,
                # code remains the monitor's author-facing compatibility field.
                "code": record.author_source,
                "author_source": record.author_source,
                "submitted_source": record.submitted_source,
                "protection_transform": record.protection_transform,
                "timeout_seconds": timeout_seconds,
                "timestamp": record.submitted_at,
            },
        )

    def _publish_execution_output(self, record: Execution, stream: str, text: str) -> None:
        safe_publish(self.monitor_publisher, {"type": "execution_output", "execution": record.execution, "stream": stream, "text": text, "kernel_execution_count": record.execution_count_at_submit, "timestamp": record.updated_at})

    def _publish_execution_finished(self, record: Execution) -> None:
        safe_publish(self.monitor_publisher, {"type": "execution_finished", "execution": record.execution, "status": record.status, "output_total_lines": record.logs.combined.line_count, "error": record.status_snapshot().get("error"), "timestamp": record.completed_at or record.updated_at})

    @staticmethod
    def _validate_timeout(timeout_seconds: float) -> dict[str, Any] | None:
        try:
            timeout = float(timeout_seconds)
        except (TypeError, ValueError):
            return {"ok": False, "status": "invalid_timeout", "message": "timeout_seconds must be greater than 0"}
        return None if math.isfinite(timeout) and timeout > 0 else {"ok": False, "status": "invalid_timeout", "message": "timeout_seconds must be greater than 0"}

    @staticmethod
    def _workspace_error(status: str, message: str, workspace: Path, python_path: Path) -> dict[str, Any]:
        return {"ok": False, "status": status, "message": message, "workspace": str(workspace), "python": str(python_path)}
