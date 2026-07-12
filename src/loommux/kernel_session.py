from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jupyter_client.blocking.client import BlockingKernelClient
from jupyter_client.connect import write_connection_file

from loommux.coding_agent_kernel import KernelLaunch
from loommux.execution import Execution


class KernelSession:
    def __init__(self, workspace: Path, python_path: Path, on_idle: Callable[[Execution], None]) -> None:
        self.workspace = workspace
        self.python_path = python_path
        self._on_idle = on_idle
        self._on_output: Callable[[Execution, str, str], None] = _ignore_output
        self._on_finished: Callable[[Execution], None] = _ignore_finished
        self.launch: KernelLaunch | None = None
        self.connection_file: str | None = None
        self.process: subprocess.Popen[str] | None = None
        self.client: BlockingKernelClient | None = None
        self.current_execution: Execution | None = None
        self.latest_execution_count = 0
        self._lock = threading.RLock()
        self._stop_collector = threading.Event()
        self._collector: threading.Thread | None = None

    def set_monitor_callbacks(self, on_output: Callable[[Execution, str, str], None], on_finished: Callable[[Execution], None]) -> None:
        self._on_output = on_output
        self._on_finished = on_finished

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process is not None else None

    def start(self, timeout_seconds: float = 10.0) -> None:
        launch = KernelLaunch.create(self.python_path, self.workspace)
        self.launch = launch
        try:
            self.connection_file, _ = write_connection_file(fname=str(launch.connection_file))
            self.process = subprocess.Popen(
                launch.command,
                cwd=str(self.workspace),
                env=launch.environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                start_new_session=True,
            )
            client = BlockingKernelClient(connection_file=self.connection_file)
            self.client = client
            client.load_connection_file()
            client.start_channels()
            client.wait_for_ready(timeout=timeout_seconds)
        except Exception:
            self.shutdown(mark_execution_killed=False)
            raise
        self._collector = threading.Thread(target=self._collect_iopub, name="loommux-iopub-collector", daemon=True)
        self._collector.start()

    def submit(self, execution: Execution) -> None:
        with self._lock:
            if self.client is None:
                raise RuntimeError("kernel client is not started")
            execution.msg_id = self.client.execute(execution.code)
            self.current_execution = execution

    def interrupt(self) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            return
        os.killpg(os.getpgid(process.pid), signal.SIGINT)

    def shutdown(self, *, mark_execution_killed: bool = True) -> None:
        with self._lock:
            execution = self.current_execution
            if mark_execution_killed and execution is not None and execution.is_running:
                execution.kill()
                self._on_finished(execution)
            self.current_execution = None
            self._stop_collector.set()
            client = self.client
            process = self.process
            connection_file = self.connection_file
            launch = self.launch

        if process is not None and process.poll() is None:
            self._terminate_process_group(process)

        if client is not None:
            try:
                client.stop_channels()
            except Exception:
                pass

        collector = self._collector
        if collector is not None and collector.is_alive() and collector is not threading.current_thread():
            collector.join(timeout=1)

        if connection_file is not None:
            Path(connection_file).unlink(missing_ok=True)
        if launch is not None:
            # The session, rather than KernelLaunch, owns removal of its root.
            shutil.rmtree(launch.runtime_root, ignore_errors=True)

        self.client = None
        self.process = None
        self.connection_file = None
        self.launch = None

    def is_alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

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
                    self._on_output(execution, "stdout", text)
                elif content.get("name") == "stderr":
                    execution.append_stderr(text)
                    self._on_output(execution, "stderr", text)
            elif msg_type in {"execute_result", "display_data"}:
                data = content.get("data", {})
                if isinstance(data, dict) and "text/plain" in data:
                    text = str(data["text/plain"])
                    execution.append_result_text(text)
                    self._on_output(execution, "result", text)
            elif msg_type == "error":
                traceback = content.get("traceback", [])
                execution.record_error(
                    {
                        "ename": content.get("ename"),
                        "evalue": content.get("evalue"),
                        "traceback": traceback if isinstance(traceback, list) else [str(traceback)],
                    }
                )
                self._on_output(execution, "traceback", execution.logs.traceback.text)
            elif msg_type == "status" and content.get("execution_state") == "idle":
                execution.finish()
                self.current_execution = None
                self._on_finished(execution)
                self._on_idle(execution)

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen[str]) -> None:
        try:
            pgid = os.getpgid(process.pid)
        except ProcessLookupError:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGTERM)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return
            time.sleep(0.05)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGKILL)
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


def _ignore_output(_execution: Execution, _stream: str, _text: str) -> None:
    pass


def _ignore_finished(_execution: Execution) -> None:
    pass
