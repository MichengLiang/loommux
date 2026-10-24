from __future__ import annotations

import math
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loommux.cell_header import DEFAULT_SILENCE_SECONDS, DEFAULT_WAIT_SECONDS, CellHeaderError, read_cell_header
from loommux.execution import Execution
from loommux.identity import KernelIdentity
from loommux.kernel.session import KernelSession

KERNEL_START_ATTEMPTS = 2
OUTPUT_STREAMS = {"combined", "stdout", "stderr", "result", "traceback"}
# Bounds how long one follow request may hold a worker thread, since every
# blocking session call shares the server's thread pool.
MAX_FOLLOW_WAIT_SECONDS = 1.0
_FOLLOW_POLL_SECONDS = 0.05
STARTUP_SUFFIXES = (".py", ".ipy")
# A startup file runs before the kernel takes its first cell, so one that never
# ends would hold that cell back for good.
STARTUP_FILE_SECONDS = 60.0
_STARTUP_INTERRUPT_SECONDS = 5.0
# Silence is meant in seconds; checking a quarter second apart keeps a long wait cheap.
_SILENCE_POLL_SECONDS = 0.25
# What a running result shows of where its output stands (silence-design 1).
RECENT_LINES = 5
RECENT_LINE_CHARACTERS = 200


class IPythonSession:
    """Own one logical resource's persistent kernel and execution history.

    The session is transport-neutral. It exposes execution facts and control
    operations to callers such as an IPython manager, a test harness, or an MCP
    consumer; it does not construct protocol responses.

    ``startup_directory`` holds the files every kernel process of this session
    runs before its first cell (docs/kernel-environment-design.md, section 5).
    """

    def __init__(self, startup_directory: Path | None = None) -> None:
        self.startup_directory = startup_directory
        self.workspace: Path | None = None
        self.workspace_resolution: str | None = None
        self.python_path: Path | None = None
        self.identity: KernelIdentity | None = None
        self.kernel: KernelSession | None = None
        self.executions: dict[int, Execution] = {}
        self.current_execution: int | None = None
        self.recent_execution: int | None = None
        self._kernel_restarting = False
        self._next_execution = 1
        # Every record numbered up to here has had its notices taken or found
        # none. Cells run one at a time, so only the newest record can still be
        # running and everything before it is final.
        self._noticed_through = 0
        # What the current kernel process's startup files said, until a result delivers it.
        self._startup_notices: list[str] = []
        # When the kernel last became idle, for reminders about kernels nobody
        # uses; None while a cell runs.
        self._idle_since: datetime | None = None
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            kernel, self.kernel = self.kernel, None
            self.current_execution = None
        if kernel is not None:
            kernel.shutdown()

    def start_workspace(self, workspace: Path, workspace_resolution: str, *, identity: KernelIdentity | None = None) -> dict[str, Any]:
        """Start the configured kernel at server lifespan start.

        ``identity`` is handed to every kernel this session starts, including
        each restart's replacement, because it names the resource rather than
        one process.
        """
        workspace = workspace.resolve(strict=False)
        python_path = Path(sys.executable).absolute()
        self._close_kernel()
        self.identity = identity
        if not workspace.exists():
            return self._workspace_error("workspace_not_found", "workspace does not exist", workspace, python_path)
        if not workspace.is_dir():
            return self._workspace_error("workspace_not_directory", "workspace path is not a directory", workspace, python_path)
        try:
            kernel = self._start_kernel(workspace, python_path)
        except TimeoutError:
            return self._workspace_error("kernel_start_timeout", "kernel did not become ready before timeout", workspace, python_path)
        except Exception as exc:
            return self._workspace_error("kernel_start_failed", f"kernel failed to start: {exc}", workspace, python_path)
        with self._lock:
            self.workspace, self.workspace_resolution, self.python_path, self.kernel = workspace, workspace_resolution, python_path, kernel
            # A server lifespan has one session. This path is startup only; restart
            # deliberately does not pass here so it cannot discard existing records.
            self.executions.clear()
            self.current_execution = None
            self.recent_execution = None
            self._next_execution = 1
            self._noticed_through = 0
            self._idle_since = datetime.now(UTC)
        return self.status()

    def run_cell(self, freeform: str, *, wait_seconds: float | None = None, silence_seconds: float | None = None) -> dict[str, Any]:
        """Submit one cell exactly as written; ``wait_seconds`` and ``silence_seconds`` override the header's.

        A program states its wait as arguments; one it leaves out is the
        header's, and the header decides complete-output delivery and is
        validated either way.
        """
        if not isinstance(freeform, str):
            return {"ok": False, "status": "invalid_code", "message": "freeform must be a string"}
        try:
            header = read_cell_header(freeform)
        except CellHeaderError as exc:
            return {"ok": False, "status": "invalid_cell_header", "message": f"invalid %%loommux header: {exc}"}
        return self._submit_python_cell(
            freeform,
            header.wait_seconds if wait_seconds is None else wait_seconds,
            header.full_output,
            header.silence_seconds if silence_seconds is None else silence_seconds,
        )

    def _submit_python_cell(self, source: str, timeout_seconds: float, full_output_requested: bool, silence_seconds: float) -> dict[str, Any]:
        if not isinstance(source, str):
            return {"ok": False, "status": "invalid_code", "message": "code must be a string"}
        # Zero is a program's "submit and return": the header's --wait is positive by its own grammar.
        if (error := self._validate_timeout(timeout_seconds, allow_zero=True) or self._validate_silence(silence_seconds)) is not None:
            return error
        with self._lock:
            if self.workspace is None:
                return {"ok": False, "status": "workspace_not_set", "message": "workspace has not been set"}
            kernel = self.kernel
            if kernel is None or not kernel.is_alive():
                return {"ok": False, "status": "kernel_not_started", "message": "kernel is not started"}
            if self.current_execution is not None:
                return {"ok": False, "status": "busy", "execution": self.current_execution, "message": "kernel is already executing code"}
            execution = Execution(
                execution=self._next_execution,
                kernel_pid=kernel.pid or 0,
                _full_output_requested=full_output_requested,
            )
            self._next_execution += 1
            self.executions[execution.execution] = execution
            self.current_execution = execution.execution
            self.recent_execution = execution.execution
            self._idle_since = None
        try:
            kernel.submit(execution, source)
        except Exception as exc:
            with self._lock:
                self.current_execution = None
                self._idle_since = datetime.now(UTC)
                execution.record_error({"ename": type(exc).__name__, "evalue": str(exc), "traceback": []})
                execution.finish()
            return self._execution_response(execution)
        # The clock starts at submission: the record was made a moment ago.
        silent = _await(execution, float(timeout_seconds), silence_seconds, since=execution.last_output_monotonic)
        return self._execution_response(execution, silent)

    def status(self) -> dict[str, Any]:
        with self._lock:
            owned_kernel = self.kernel
            kernel = owned_kernel if owned_kernel is not None and owned_kernel.is_alive() else None
            if owned_kernel is not None and kernel is None:
                running = self.executions.get(self.current_execution) if self.current_execution is not None else None
                if running is not None and running.is_running:
                    running.record_kernel_exit(owned_kernel.runtime.returncode)
                self.current_execution = None
            return {
                "ok": True,
                "workspace": str(self.workspace) if self.workspace is not None else None,
                "workspace_resolution": self.workspace_resolution,
                "python": str(self.python_path) if self.python_path is not None else None,
                "kernel_started": kernel is not None,
                "kernel_restarting": self._kernel_restarting,
                "kernel_pid": kernel.pid if kernel is not None else None,
                "busy": self.current_execution is not None,
                "current_execution": self.current_execution,
                "recent_execution": self.recent_execution,
                "idle_since": None if self._idle_since is None else self._idle_since.isoformat(),
            }

    def execution_status(self, execution: int | None = None) -> dict[str, Any]:
        record = self._select_execution(execution)
        return {"ok": False, "status": "execution_not_found", "message": "execution was not found"} if record is None else self._status_response(record)

    def read_output(self, execution: int | None = None, stream: str = "combined", line_range: str | None = None, max_chars: int | None = None) -> dict[str, Any]:
        record, error = self._select_stream(execution, stream)
        if error is not None:
            return error
        assert record is not None
        line_log = record.logs.get(stream)
        assert line_log is not None
        result = line_log.read(line_range, max_chars=max_chars)
        if result.get("ok") is not False:
            result.update({"execution": record.execution, "stream": stream})
        return result

    def search_output(self, query: str, execution: int | None = None, stream: str = "combined", query_mode: str = "auto", context_before: int = 0, context_after: int = 0, ignore_case: bool = False, max_chars: int | None = None) -> dict[str, Any]:
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

    def wait(self, execution: int | None = None, timeout_seconds: float = DEFAULT_WAIT_SECONDS, silence_seconds: float = DEFAULT_SILENCE_SECONDS) -> dict[str, Any]:
        if (error := self._validate_timeout(timeout_seconds) or self._validate_silence(silence_seconds)) is not None:
            return error
        called = time.monotonic()
        record = self._select_execution(execution)
        if record is None:
            return {"ok": False, "status": "execution_not_found", "message": "execution was not found", "kernel": self._kernel_status()}
        # Silence counts from the later of this call and the last output: measured from
        # the output alone, waiting again on a quiet stretch would return at once.
        silent = _await(record, float(timeout_seconds), silence_seconds, since=called)
        return self._execution_response(record, silent)

    def output_since(self, execution: int, offset: int, wait_seconds: float = MAX_FOLLOW_WAIT_SECONDS) -> dict[str, Any]:
        """Combined output after the first ``offset`` characters, for following a cell.

        Returns as soon as new text exists or the execution ends, and at most
        after ``wait_seconds`` (capped at ``MAX_FOLLOW_WAIT_SECONDS``).
        """
        record = self._select_execution(execution)
        if record is None:
            return {"ok": False, "status": "execution_not_found", "message": "execution was not found"}
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            return {"ok": False, "status": "invalid_offset", "message": "offset must be a non-negative integer"}
        log = record.logs.combined
        deadline = time.monotonic() + min(max(wait_seconds, 0.0), MAX_FOLLOW_WAIT_SECONDS)
        while log.character_count <= offset and record.is_running and (remaining := deadline - time.monotonic()) > 0:
            record.done.wait(min(_FOLLOW_POLL_SECONDS, remaining))
        # Status before text: output ends before an execution is marked
        # finished, so text read after a terminal status is all of it, and text
        # read while running is completed by the next request. Not
        # status_snapshot(): it counts the log's lines, which a follower asking
        # after every chunk would pay for the whole output each time.
        status, error = record.status, record.error_summary
        text = log.text_since(offset)
        result = {"ok": True, "execution": record.execution, "status": status, "error": error, "text": text, "offset": offset + len(text)}
        if status != "running":
            # Handed to the follower with the final state: a forwarding cell
            # speaks them again, since no MCP session works in a child kernel.
            result["notices"] = self.take_notices_of(record.execution)
        return result

    def interrupt(self) -> dict[str, Any]:
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
            self._kernel_restarting = True
            self.current_execution = None
            record.record_error({"ename": "KeyboardInterrupt", "evalue": "", "traceback": []})
            record.finish()
            # A new process numbers its cells from 1, exactly as after restart().
            self._forget_records_locked()
            workspace, python_path = self.workspace, self.python_path
        try:
            kernel = self._replace_kernel(old_kernel, workspace, python_path)
        except Exception as exc:
            return self._workspace_error("kernel_start_failed", f"kernel failed to restart after interrupt: {exc}", workspace, python_path)
        return {"ok": True, "status": "interrupt_sent", "execution": record.execution, "kernel_pid": kernel.pid}

    def restart(self) -> dict[str, Any]:
        with self._lock:
            if self.workspace is None or self.python_path is None:
                return {"ok": False, "status": "workspace_not_set", "message": "workspace has not been set"}
            workspace, python_path, old_kernel = self.workspace, self.python_path, self.kernel
            running = self.executions.get(self.current_execution) if self.current_execution is not None else None
            self.kernel = None
            self._kernel_restarting = True
            self.current_execution = None
            if running is not None and running.is_running:
                running.kill()
            self._forget_records_locked()
        try:
            kernel = self._replace_kernel(old_kernel, workspace, python_path)
        except Exception as exc:
            return self._workspace_error("kernel_start_failed", f"kernel failed to restart: {exc}", workspace, python_path)
        return {"ok": True, "status": "restarted", "workspace": str(workspace), "workspace_resolution": self.workspace_resolution, "python": str(python_path), "kernel_started": True, "kernel_pid": kernel.pid, "busy": False, "current_execution": None, "recent_execution": self.recent_execution}

    def take_notices(self) -> list[dict[str, Any]]:
        """The notices no result has delivered yet, each handed out exactly once.

        A record's notices are final when it is: a cell still running may speak
        again, so its notices wait for whichever result follows its end.
        """
        taken: list[dict[str, Any]] = []
        with self._lock:
            taken.extend({"execution": None, "text": text} for text in self._startup_notices)
            self._startup_notices = []
            number = self._noticed_through + 1
            while (record := self.executions.get(number)) is not None and not record.is_running:
                if not record.notices_delivered:
                    record.notices_delivered = True
                    taken.extend({"execution": number, "text": text} for text in record.notice_texts())
                self._noticed_through = number
                number += 1
        return taken

    def take_notices_of(self, execution: int) -> list[str]:
        """One final execution's notices, for a caller that followed that execution to its end."""
        with self._lock:
            record = self.executions.get(execution)
            if record is None or record.is_running or record.notices_delivered:
                return []
            record.notices_delivered = True
            return record.notice_texts()

    def _forget_records_locked(self) -> None:
        """Begin the count again with the kernel process that is about to replace this one.

        IPython numbers the new process's cells from 1, and a loommux execution
        is IPython's number, so records of the old process cannot stay: two of
        them would answer to one number. What the agent saw of them is in its
        own transcript.
        """
        self.executions.clear()
        self.recent_execution = None
        self._next_execution = 1
        self._noticed_through = 0

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
                self._idle_since = datetime.now(UTC)

    def _on_kernel_exit(
        self,
        record: Execution | None,
        returncode: int | None,
    ) -> None:
        with self._lock:
            if record is not None and record.is_running:
                record.record_kernel_exit(returncode)
            if record is not None and self.current_execution == record.execution:
                self.current_execution = None
                self._idle_since = datetime.now(UTC)

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

    def _execution_response(self, record: Execution, silent: float | None = None) -> dict[str, Any]:
        result = record.snapshot()
        if not record.is_running and record.has_rich_presentation:
            # This is consumed by the MCP result projection only. Keeping it private prevents
            # Base64 payloads from leaking into status JSON or text logs.
            result["_presentation"] = tuple(record.presentation)
        if record.is_running:
            # Where the output stands, so a wait that ended early or ran out shows how far the cell got.
            result["recent_lines"] = _recent_lines(record)
            if silent is not None:
                result["silent_seconds"] = round(silent, 1)
        result["kernel"] = self._kernel_status()
        return result

    def _status_response(self, record: Execution) -> dict[str, Any]:
        result = record.status_snapshot()
        result["kernel"] = self._kernel_status()
        return result

    def _kernel_status(self) -> dict[str, Any]:
        kernel = self.kernel
        return {"busy": self.current_execution is not None, "kernel_pid": kernel.pid if kernel is not None else None}

    def _new_kernel_session(self, workspace: Path, python_path: Path) -> KernelSession:
        return KernelSession(
            workspace,
            python_path,
            self._on_execution_idle,
            self._on_kernel_exit,
            self.identity,
        )

    def _replace_kernel(self, old_kernel: KernelSession | None, workspace: Path, python_path: Path) -> KernelSession:
        """Install a new kernel after the caller detached ``old_kernel``.

        The caller sets ``_kernel_restarting`` in the same locked step that
        detaches the old kernel, so ``status()`` reports the gap as a restart
        rather than as a stopped kernel. This clears it whether the start
        succeeds or raises; after a failure the session has no kernel.
        """
        try:
            if old_kernel is not None:
                old_kernel.shutdown(mark_execution_killed=False)
            kernel = self._start_kernel(workspace, python_path)
            with self._lock:
                self.kernel = kernel
                self._idle_since = datetime.now(UTC)
            return kernel
        finally:
            with self._lock:
                self._kernel_restarting = False

    def _start_kernel(self, workspace: Path, python_path: Path) -> KernelSession:
        """Start a new kernel process, giving a failed start one fresh process.

        A kernel can miss its first readiness handshake while its ZMQ channels
        initialize, and over TCP another process can take a port between
        Jupyter choosing it and the kernel binding it. Neither says anything
        about the workspace, so every start (first start, restart, and the
        Windows interrupt replacement) retries before reporting failure. A
        failed ``KernelSession.start`` has already shut itself down.
        """
        for _attempt in range(KERNEL_START_ATTEMPTS - 1):
            kernel = self._new_kernel_session(workspace, python_path)
            try:
                kernel.start()
            except Exception:
                continue
            return self._started(kernel)
        kernel = self._new_kernel_session(workspace, python_path)
        kernel.start()
        return self._started(kernel)

    def _started(self, kernel: KernelSession) -> KernelSession:
        """Run the startup files in a new kernel process, and keep what they said."""
        notices = self._run_startup_files(kernel)
        with self._lock:
            # The previous process's undelivered startup notices go with it.
            self._startup_notices = notices
        return kernel

    def _run_startup_files(self, kernel: KernelSession) -> list[str]:
        """Each file is loommux's own request, so whatever it prints or raises has an owner.

        IPython's own exec_files run while the kernel initializes, before any
        request exists, where a failure would reach nobody. A request that
        stores no history also takes no number, so the first cell is In [1].
        """
        directory = self.startup_directory
        if directory is None or not directory.is_dir():
            return []
        notices: list[str] = []
        for path in sorted(path for path in directory.iterdir() if path.suffix in STARTUP_SUFFIXES and path.is_file()):
            # Never registered as an execution: number 0 is no cell's.
            record = Execution(execution=0, kernel_pid=kernel.pid or 0)
            kernel.submit(record, _startup_source(path), store_history=False)
            if not record.done.wait(STARTUP_FILE_SECONDS):
                kernel.interrupt()
                stopped = record.done.wait(_STARTUP_INTERRUPT_SECONDS)
                notices += [*record.notice_texts(), f"{path.name} failed: it did not finish within {STARTUP_FILE_SECONDS:g} s and was interrupted"]
                if not stopped:
                    # The kernel still runs it; nothing else can be sent.
                    break
                continue
            notices += record.notice_texts()
            if record.error is not None:
                detail = ": ".join(str(part) for part in (record.error.get("ename"), record.error.get("evalue")) if part)
                notices.append(f"{path.name} failed: {detail}")
        return notices

    @staticmethod
    def _validate_timeout(timeout_seconds: float, *, allow_zero: bool = False) -> dict[str, Any] | None:
        message = "timeout_seconds must be 0 or more" if allow_zero else "timeout_seconds must be greater than 0"
        try:
            timeout = float(timeout_seconds)
        except (TypeError, ValueError):
            timeout = math.nan
        valid = math.isfinite(timeout) and (timeout >= 0 if allow_zero else timeout > 0)
        return None if valid else {"ok": False, "status": "invalid_timeout", "message": message}

    @staticmethod
    def _validate_silence(silence_seconds: float) -> dict[str, Any] | None:
        try:
            silence = float(silence_seconds)
        except (TypeError, ValueError):
            silence = math.nan
        return None if math.isfinite(silence) and silence > 0 else {"ok": False, "status": "invalid_silence", "message": "silence_seconds must be greater than 0"}

    @staticmethod
    def _workspace_error(status: str, message: str, workspace: Path, python_path: Path) -> dict[str, Any]:
        return {"ok": False, "status": status, "message": message, "workspace": str(workspace), "python": str(python_path)}


def _startup_source(path: Path) -> str:
    """The request that runs one startup file in the user namespace, as IPython's startup/ would."""
    if path.suffix == ".ipy":
        return f"get_ipython().safe_execfile_ipy({str(path)!r}, raise_exceptions=True)"
    return f"get_ipython().safe_execfile({str(path)!r}, get_ipython().user_ns, raise_exceptions=True)"


def _await(record: Execution, timeout_seconds: float, silence_seconds: float, *, since: float) -> float | None:
    """Wait for the execution's end, ``timeout_seconds``, or silence, whichever comes first.

    Returns how long the execution had been silent when silence ended the wait,
    else None. Silence counts from the later of ``since`` and its last output.
    """
    deadline = time.monotonic() + timeout_seconds
    while record.is_running:
        now = time.monotonic()
        quiet = now - max(since, record.last_output_monotonic)
        # Silence first: when both timers expire together, as the wait tool's equal defaults
        # always do on a quiet run, "silent for N s" tells stuck from slow and a bare expiry does not.
        if quiet >= silence_seconds:
            return quiet
        if now >= deadline:
            return None
        record.done.wait(min(deadline - now, silence_seconds - quiet, _SILENCE_POLL_SECONDS))
    return None


def _recent_lines(record: Execution) -> list[str]:
    """The last few lines of combined output, each cut short; read from the tail, not the whole log."""
    log = record.logs.combined
    tail = log.text_since(max(0, log.character_count - RECENT_LINES * RECENT_LINE_CHARACTERS * 2))
    lines = [line for line in tail.splitlines() if line.strip()][-RECENT_LINES:]
    return [line if len(line) <= RECENT_LINE_CHARACTERS else line[: RECENT_LINE_CHARACTERS - 1] + "…" for line in lines]
