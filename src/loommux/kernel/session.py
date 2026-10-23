"""Correlate one IPython kernel client with the active execution record.

This module translates Jupyter messages into execution facts. Kernel
process lifecycle remains in ``kernel.runtime``; session-level record
selection and numbering remain in ``loommux.session``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jupyter_client.blocking.client import BlockingKernelClient

from loommux.execution import Execution
from loommux.identity import KernelIdentity
from loommux.kernel.launch import KernelLaunch
from loommux.kernel.runtime import KernelRuntime, close_jupyter_event_loop
from loommux.notice import NOTICE_MIME

# ipykernel sends execute_reply before it publishes idle, so at idle the reply
# is already on its way; this only bounds a kernel that never sends one.
REPLY_WAIT_SECONDS = 2.0


class KernelSession:
    """Bridge one live kernel client to the session's active execution record.

    This object deliberately does not allocate public execution identifiers or
    format consumer responses. Its only coordination responsibility is to
    correlate Jupyter messages with the submitted cell and notify the owning
    session when the kernel reports the execution as idle.
    """

    def __init__(
        self,
        workspace: Path,
        python_path: Path,
        on_idle: Callable[[Execution], None],
        on_kernel_exit: Callable[[Execution | None, int | None], None],
        identity: KernelIdentity | None = None,
    ) -> None:
        self.workspace = workspace
        self.python_path = python_path
        self._on_idle = on_idle
        self._on_kernel_exit = on_kernel_exit
        self.runtime = KernelRuntime(workspace, python_path, identity)
        self.client: BlockingKernelClient | None = None
        self.current_execution: Execution | None = None
        self._lock = threading.RLock()
        self._stop_collector = threading.Event()
        self._collector: threading.Thread | None = None
        self._exit_notified = False

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

    def submit(self, execution: Execution, source: str, *, store_history: bool = True) -> None:
        """Send ``source``; a request that stores no history takes no IPython number."""
        with self._lock:
            if self.client is None:
                raise RuntimeError("kernel client is not started")
            execution.msg_id = self.client.execute(source, store_history=store_history)
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

    def _collect_iopub(self) -> None:
        # get_iopub_msg caches a jupyter_core event loop in this thread's own
        # context; close it when the collector ends rather than leaving it to a
        # full garbage collection.
        try:
            self._collect_iopub_messages()
        finally:
            close_jupyter_event_loop()

    def _collect_iopub_messages(self) -> None:
        while not self._stop_collector.is_set():
            client = self.client
            if client is None:
                return
            try:
                message = client.get_iopub_msg(timeout=0.1)
            except Exception:
                # A dead kernel does not fail its iopub channel; the channel only
                # goes quiet. Every empty poll therefore checks the process, which
                # is why KernelRuntime.is_alive must stay a plain process poll.
                if not self.is_alive():
                    self._notify_kernel_exit()
                    return
                continue
            self._handle_message(message)

    def _notify_kernel_exit(self) -> None:
        with self._lock:
            if self._exit_notified or self._stop_collector.is_set():
                return
            self._exit_notified = True
            execution = self.current_execution
            self.current_execution = None
        self._on_kernel_exit(execution, self.runtime.returncode)

    def _handle_message(self, message: dict[str, Any]) -> None:
        msg_type = message.get("msg_type")
        content = message.get("content", {})
        parent_msg_id = message.get("parent_header", {}).get("msg_id")
        finished: Execution | None = None
        with self._lock:
            execution = self.current_execution
            if execution is None or parent_msg_id != execution.msg_id:
                return
            if msg_type == "stream":
                text = str(content.get("text", ""))
                if content.get("name") == "stdout":
                    execution.append_stdout(text)
                elif content.get("name") == "stderr":
                    execution.append_stderr(text)
            elif msg_type in {"execute_result", "display_data"}:
                data = content.get("data", {})
                if msg_type == "display_data" and isinstance(data, dict) and NOTICE_MIME in data:
                    execution.append_notice(str(data[NOTICE_MIME]))
                    return
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
                finished = execution
        if finished is None:
            return
        # Outside the lock: reading the reply may wait, and shutdown needs it.
        page = self._reply_pages(finished)
        with self._lock:
            if self.current_execution is not finished:
                # A shutdown during the reply wait already killed and released it.
                return
            if page:
                finished.append_stdout(page)
            finished.finish()
            self.current_execution = None
        self._on_idle(finished)

    def _reply_pages(self, execution: Execution) -> str:
        """Text that the execute_reply asks a frontend to show in its pager.

        IPython answers ``obj?``, ``obj??``, and ``%%magic?`` this way rather
        than on iopub, so without it every help request printed nothing.
        Reading each reply also keeps replies from piling up unread in the
        shell channel.
        """
        client = self.client
        deadline = time.monotonic() + REPLY_WAIT_SECONDS
        while client is not None and (remaining := deadline - time.monotonic()) > 0:
            try:
                reply = client.get_shell_msg(timeout=remaining)
            except Exception:
                # queue.Empty when no reply arrives in time; a shutdown closing
                # the channel meanwhile raises whatever the channel raises, and
                # either way the execution still has to finish.
                return ""
            if reply.get("parent_header", {}).get("msg_id") != execution.msg_id:
                continue
            pages = [
                str(payload.get("data", {}).get("text/plain", ""))
                for payload in reply.get("content", {}).get("payload", [])
                if payload.get("source") == "page"
            ]
            return "".join(page if page.endswith("\n") else f"{page}\n" for page in pages if page)
        return ""
