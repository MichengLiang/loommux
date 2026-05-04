from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from loommux.execution import Execution
from loommux.kernel_session import KernelSession


class IPythonMCPAdapter:
    def __init__(self) -> None:
        self.workspace: Path | None = None
        self.python_path: Path | None = None
        self.kernel: KernelSession | None = None
        self.executions: dict[str, Execution] = {}
        self.current_execution_id: str | None = None
        self.last_execution_id: str | None = None
        self._next_execution_number = 1
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            kernel = self.kernel
            self.kernel = None
            self.current_execution_id = None
        if kernel is not None:
            kernel.shutdown()

    def set_workspace(self, path: str) -> dict[str, Any]:
        workspace = self._resolve_workspace(path)
        self._close_kernel_before_workspace_switch()
        if not workspace.exists():
            return self._workspace_error("workspace_not_found", "workspace does not exist", workspace, workspace / ".venv" / "bin" / "python")
        if not workspace.is_dir():
            return self._workspace_error("workspace_not_directory", "workspace path is not a directory", workspace, workspace / ".venv" / "bin" / "python")
        python_path = workspace / ".venv" / "bin" / "python"
        python_check = self._check_workspace_python(workspace, python_path)
        if python_check is not None:
            return python_check

        kernel = KernelSession(workspace, python_path, self._on_execution_idle)
        with self._lock:
            self.workspace = workspace
            self.python_path = python_path
        try:
            kernel.start()
        except TimeoutError:
            return self._workspace_error("kernel_start_timeout", "kernel did not become ready before timeout", workspace, python_path)
        except Exception as exc:
            status = "kernel_start_timeout" if "timed out" in str(exc).lower() else "kernel_start_failed"
            return self._workspace_error(status, f"kernel failed to start: {exc}", workspace, python_path)

        with self._lock:
            self.workspace = workspace
            self.python_path = python_path
            self.kernel = kernel
            self.executions.clear()
            self.current_execution_id = None
            self.last_execution_id = None
            self._next_execution_number = 1
        return {
            "ok": True,
            "workspace": str(workspace),
            "python": str(python_path),
            "kernel_started": True,
            "kernel_pid": kernel.pid,
            "busy": False,
            "current_execution_id": None,
            "execution_count": 0,
        }

    def _close_kernel_before_workspace_switch(self) -> None:
        with self._lock:
            old_kernel = self.kernel
            if old_kernel is None:
                return
            self.kernel = None
            self.workspace = None
            self.python_path = None
            self.current_execution_id = None
        old_kernel.shutdown()

    def run_python(self, code: str, timeout_seconds: float = 30) -> dict[str, Any]:
        if not isinstance(code, str):
            return {"ok": False, "status": "invalid_code", "message": "code must be a string"}
        timeout_error = self._validate_timeout(timeout_seconds)
        if timeout_error is not None:
            return timeout_error
        with self._lock:
            if self.workspace is None:
                return {"ok": False, "status": "workspace_not_set", "message": "workspace has not been set"}
            kernel = self.kernel
            if kernel is None or not kernel.is_alive():
                return {"ok": False, "status": "kernel_not_started", "message": "kernel is not started"}
            if self.current_execution_id is not None:
                return {"ok": False, "status": "busy", "current_execution_id": self.current_execution_id, "message": "kernel is already executing code"}
            execution = Execution(execution_id=self._new_execution_id(), code=code, kernel_pid=kernel.pid or 0)
            self.executions[execution.execution_id] = execution
            self.current_execution_id = execution.execution_id
            self.last_execution_id = execution.execution_id
        try:
            kernel.submit(execution)
        except Exception as exc:
            with self._lock:
                self.current_execution_id = None
                execution.record_error({"ename": type(exc).__name__, "evalue": str(exc), "traceback": []})
                execution.finish()
            return self._execution_response(execution)

        execution.done.wait(float(timeout_seconds))
        return self._execution_response(execution)

    def python_status(self) -> dict[str, Any]:
        with self._lock:
            kernel = self.kernel
            if kernel is not None and not kernel.is_alive():
                kernel = None
                self.kernel = None
                self.current_execution_id = None
            return {
                "ok": True,
                "workspace": str(self.workspace) if self.workspace is not None else None,
                "python": str(self.python_path) if self.python_path is not None else None,
                "kernel_started": kernel is not None,
                "kernel_pid": kernel.pid if kernel is not None else None,
                "busy": self.current_execution_id is not None,
                "current_execution_id": self.current_execution_id,
                "execution_count": kernel.latest_execution_count if kernel is not None else 0,
                "last_execution_id": self.last_execution_id,
            }

    def read_python_output(self, execution_id: str | None = None) -> dict[str, Any]:
        execution = self._select_execution(execution_id)
        if execution is None:
            return {"ok": False, "status": "execution_not_found", "message": "execution was not found"}
        return execution.snapshot()

    def wait_python(self, execution_id: str | None = None, timeout_seconds: float = 30) -> dict[str, Any]:
        timeout_error = self._validate_timeout(timeout_seconds)
        if timeout_error is not None:
            return timeout_error
        execution = self._select_execution(execution_id)
        if execution is None:
            return {"ok": False, "status": "execution_not_found", "message": "execution was not found", "kernel": self._kernel_wait_status()}
        if execution.is_running:
            execution.done.wait(float(timeout_seconds))
        snapshot = execution.snapshot()
        snapshot["kernel"] = self._kernel_wait_status()
        return snapshot

    def interrupt_python(self) -> dict[str, Any]:
        with self._lock:
            kernel = self.kernel
            if kernel is None or not kernel.is_alive():
                return {"ok": False, "status": "kernel_not_started", "message": "kernel is not started"}
            if self.current_execution_id is None:
                return {"ok": True, "status": "idle", "kernel_pid": kernel.pid}
            execution = self.executions[self.current_execution_id]
            execution.interrupt_requested = True
        kernel.interrupt()
        return {"ok": True, "status": "interrupt_sent", "execution_id": execution.execution_id, "kernel_pid": kernel.pid}

    def reset_python(self) -> dict[str, Any]:
        with self._lock:
            if self.workspace is None or self.python_path is None:
                return {"ok": False, "status": "workspace_not_set", "message": "workspace has not been set"}
            workspace = self.workspace
            python_path = self.python_path
            old_kernel = self.kernel
            old_current_id = self.current_execution_id
            self.kernel = None
            self.current_execution_id = None
            if old_current_id is not None and old_current_id in self.executions:
                self.executions[old_current_id].kill()
        if old_kernel is not None:
            old_kernel.shutdown()

        kernel = KernelSession(workspace, python_path, self._on_execution_idle)
        try:
            kernel.start()
        except Exception as exc:
            return self._workspace_error("kernel_start_failed", f"kernel failed to restart: {exc}", workspace, python_path)
        with self._lock:
            self.kernel = kernel
        return {
            "ok": True,
            "status": "restarted",
            "workspace": str(workspace),
            "python": str(python_path),
            "kernel_started": True,
            "kernel_pid": kernel.pid,
            "busy": False,
            "current_execution_id": None,
            "execution_count": 0,
        }

    def _on_execution_idle(self, execution: Execution) -> None:
        with self._lock:
            if self.current_execution_id == execution.execution_id:
                self.current_execution_id = None

    def _select_execution(self, execution_id: str | None) -> Execution | None:
        with self._lock:
            selected_id = execution_id or self.current_execution_id or self.last_execution_id
            if selected_id is None:
                return None
            return self.executions.get(selected_id)

    def _execution_response(self, execution: Execution) -> dict[str, Any]:
        snapshot = execution.snapshot()
        snapshot["kernel"] = self.kernel.kernel_info() if self.kernel is not None else {"busy": False, "kernel_pid": None, "execution_count": 0}
        return snapshot

    def _kernel_wait_status(self) -> dict[str, Any]:
        kernel = self.kernel
        return {"busy": self.current_execution_id is not None, "kernel_pid": kernel.pid if kernel is not None else None}

    def _new_execution_id(self) -> str:
        execution_id = f"exec-{self._next_execution_number:06d}"
        self._next_execution_number += 1
        return execution_id

    @staticmethod
    def _resolve_workspace(path: str) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        return candidate.resolve(strict=False)

    def _check_workspace_python(self, workspace: Path, python_path: Path) -> dict[str, Any] | None:
        if not python_path.exists():
            return self._workspace_error("python_not_found", "workspace Python does not exist", workspace, python_path)
        if not os.access(python_path, os.X_OK):
            return self._workspace_error("python_not_executable", "workspace Python is not executable", workspace, python_path)
        try:
            result = subprocess.run([str(python_path), "-c", "import ipykernel"], cwd=str(workspace), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        except subprocess.TimeoutExpired:
            return self._workspace_error("ipykernel_missing", "workspace Python timed out while importing ipykernel", workspace, python_path)
        except OSError as exc:
            return self._workspace_error("python_not_executable", f"workspace Python cannot be executed: {exc}", workspace, python_path)
        if result.returncode != 0:
            return self._workspace_error("ipykernel_missing", "workspace Python cannot import ipykernel", workspace, python_path)
        return None

    @staticmethod
    def _validate_timeout(timeout_seconds: float) -> dict[str, Any] | None:
        try:
            timeout = float(timeout_seconds)
        except (TypeError, ValueError):
            return {"ok": False, "status": "invalid_timeout", "message": "timeout_seconds must be greater than 0"}
        if timeout <= 0:
            return {"ok": False, "status": "invalid_timeout", "message": "timeout_seconds must be greater than 0"}
        return None

    @staticmethod
    def _workspace_error(status: str, message: str, workspace: Path, python_path: Path) -> dict[str, Any]:
        return {"ok": False, "status": status, "message": message, "workspace": str(workspace), "python": str(python_path)}
