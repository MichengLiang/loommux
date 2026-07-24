from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jupyter_client.blocking.client import BlockingKernelClient

from loommux.coding_agent_kernel import KernelLaunch
from loommux.execution import Execution
from loommux.kernel_runtime import KernelRuntime


class KernelSession:
    def __init__(self, workspace: Path, python_path: Path, on_idle: Callable[[Execution], None]) -> None:
        self.workspace = workspace
        self.python_path = python_path
        self._on_idle = on_idle
        self.runtime = KernelRuntime(workspace, python_path)
        self.client: BlockingKernelClient | None = None
        self.current_execution: Execution | None = None
        self.latest_execution_count = 0
        self._lock = threading.RLock()
        self._stop_collector = threading.Event()
        self._collector: threading.Thread | None = None

    @property
    def pid(self) -> int | None:
        return self.runtime.pid

    @property
    def launch(self) -> KernelLaunch | None:
        return self.runtime.launch

    def start(self, timeout_seconds: float = 10.0) -> None:
        try:
            self.client = self.runtime.start(timeout_seconds)
        except Exception:
            self.shutdown(mark_execution_killed=False)
            raise
        self._collector = threading.Thread(target=self._collect_iopub, name="loommux-iopub-collector", daemon=True)
        self._collector.start()

    def submit(self, execution: Execution) -> None:
        with self._lock:
            if self.client is None:
                raise RuntimeError("kernel client is not started")
            execution.msg_id = self.client.execute(execution.submitted_source or execution.code)
            self.current_execution = execution

    def interrupt(self) -> None:
        self.runtime.interrupt()

    def shutdown(self, *, mark_execution_killed: bool = True) -> None:
        with self._lock:
            execution = self.current_execution
            if mark_execution_killed and execution is not None and execution.is_running:
                execution.kill()
            self.current_execution = None
            self._stop_collector.set()
            self.client = None

        self.runtime.shutdown()

        collector = self._collector
        if collector is not None and collector.is_alive() and collector is not threading.current_thread():
            collector.join(timeout=1)

    def is_alive(self) -> bool:
        return self.runtime.is_alive()

    def kernel_info(self) -> dict[str, Any]:
        return {
            "busy": self.current_execution is not None and self.current_execution.is_running,
            "kernel_pid": self.pid,
            "execution_count": self.latest_execution_count,
        }

    def _collect_iopub(self) -> None:
        while not self._stop_collector.is_set():
            client = self.client
            if client is None:
                return
            try:
                message = client.get_iopub_msg(timeout=0.1)
            except Exception:
                if not self.is_alive():
                    return
                continue
            self._handle_message(message)

    def _handle_message(self, message: dict[str, Any]) -> None:
        msg_type = message.get("msg_type")
        content = message.get("content", {})
        parent_msg_id = message.get("parent_header", {}).get("msg_id")
        with self._lock:
            execution = self.current_execution
            if execution is None or parent_msg_id != execution.msg_id:
                return
            if msg_type == "execute_input":
                count = content.get("execution_count")
                if isinstance(count, int):
                    execution.execution_count_at_submit = count
                    self.latest_execution_count = count
            elif msg_type == "stream":
                text = str(content.get("text", ""))
                if content.get("name") == "stdout":
                    execution.append_stdout(text)
                elif content.get("name") == "stderr":
                    execution.append_stderr(text)
            elif msg_type in {"execute_result", "display_data"}:
                data = content.get("data", {})
                if isinstance(data, dict) and "text/plain" in data:
                    text = str(data["text/plain"])
                    execution.append_result_text(text)
                execution.append_display_data(data, content.get("metadata", {}))
            elif msg_type == "error":
                traceback = content.get("traceback", [])
                execution.record_error(
                    {
                        "ename": content.get("ename"),
                        "evalue": content.get("evalue"),
                        "traceback": traceback if isinstance(traceback, list) else [str(traceback)],
                    }
                )
            elif msg_type == "status" and content.get("execution_state") == "idle":
                execution.finish()
                self.current_execution = None
                self._on_idle(execution)
