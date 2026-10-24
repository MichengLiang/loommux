"""Control a Loommux daemon from Python; :class:`Daemon` is the map.

The rules behind it are in docs/kernel-control-surface-design.md.
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from loommux.cell_header import DEFAULT_SILENCE_SECONDS, DEFAULT_WAIT_SECONDS, CellHeaderError, read_cell_header
from loommux.identity import CONTROL_URL_ENV, KernelIdentity
from loommux.notice import NOTICE_MIME

if TYPE_CHECKING:
    import httpx

DEFAULT_CONTROL_URL = "http://127.0.0.1:8801"
# Restart starts a new kernel process and retries one failed start.
_CONTROL_TIMEOUT_SECONDS = 60.0


class DaemonError(RuntimeError):
    """The daemon refused a request or could not be reached.

    ``status`` is the session's machine-readable reason when it gave one,
    such as ``busy`` for a kernel that is already running a cell.
    """

    def __init__(self, message: str, status: str | None = None) -> None:
        super().__init__(message)
        self.status = status


class Daemon:
    """A client of the Loommux daemon, as the ``docker`` command is of dockerd.

    Every Loommux kernel, child kernels included, holds one as ``loommux``,
    bound to the daemon that started it. Outside a kernel, ``Daemon()``
    connects to ``LOOMMUX_CONTROL_URL`` or to ``http://127.0.0.1:8801``.

    The daemon keeps the registry, so a kernel's birth, death and name are
    its buttons. A :class:`Kernel` it hands out has the eight operations of
    the MCP tools, with their names and parameters, so what an agent does to
    its main kernel through the tools, code does to any kernel::

        # the daemon's buttons
        loommux.kernels()                  # every kernel, as the console lists them
        loommux["build"]                   # find by name, path or id; never opens
        build = loommux.create("build")    # open a child kernel of this main kernel, or find it
        loommux.rename(loommux.self, "report")
        loommux.recycle("build", force=False)
        loommux.recycle_idle(); loommux.recycle_all()

        # a kernel's buttons
        n = build.run_cell("make()", wait_seconds=0)["execution"]
        build.wait(n, timeout_seconds=600, silence_seconds=60)
        build.read_output(n, line_range="-20:")["text"]
        build.search_output("error", n, ignore_case=True)["text"]
        build.interrupt(); build.restart()

        loommux.notify("⚠ memory is low")  # to the agent, apart from this cell's output

    A name is a path. ``/main`` and ``/main/child`` are read from the root.
    A bare name is read from the caller's main kernel: that main kernel's own
    name or one of its child kernels'; outside a kernel it is a main kernel's.
    An id names its kernel anywhere. Names change and ids do not, so a
    reference that lasts keeps the id. Only an MCP session opens a main kernel
    (``%%loommux --attach NAME --create``), because only its lease keeps one.

    ``identity`` is the kernel this process is, read from its environment,
    or ``None`` outside a kernel; a script started from a kernel inherits it
    and acts as that kernel. ``url`` is the daemon's HTTP origin, or ``None``
    under a stdio daemon, which serves no control plane.

    The client speaks the daemon's HTTP control plane, so a mistake made
    through it stays in the calling process and never stops the daemon's
    event loop. A request the daemon refuses, or a daemon that cannot be
    reached, raises :class:`DaemonError`.
    """

    def __init__(self, url: str | None = None) -> None:
        self.identity = KernelIdentity.from_environ()
        if url is not None:
            self.url: str | None = url.rstrip("/")
        elif self.identity is not None:
            self.url = self.identity.control_url
        else:
            self.url = os.environ.get(CONTROL_URL_ENV) or DEFAULT_CONTROL_URL
        self._http: httpx.Client | None = None

    def kernels(self, *, timeout: float = _CONTROL_TIMEOUT_SECONDS) -> Kernels:
        """Every kernel, oldest first, as the console lists them; a child kernel's row names its home.

        ``timeout`` is how many seconds to wait for the daemon before
        ``DaemonError``. Code that asks on every cell passes a short one, so a
        stuck daemon costs it one missed answer rather than a minute per cell.
        """
        resources = self._request("GET", "/api/resources", timeout=timeout)["resources"]
        return Kernels(self._handle(resource) for resource in resources)

    def __getitem__(self, path: str) -> Kernel:
        """The kernel ``path`` names; opens nothing.

        An id names its kernel anywhere; ``/main`` and ``main/child`` are read
        from the root; a bare name is read from this kernel's main kernel, or
        outside a kernel names a main kernel. A path naming nothing raises
        ``DaemonError`` with status ``kernel_not_found``.
        """
        params = {"path": path} | ({} if self.identity is None else {"context": self.identity.kernel_id})
        return self._handle(self._request("GET", "/api/resources/lookup", params=params)["resource"])

    def create(self, path: str) -> Kernel:
        """The kernel ``path`` names, opening a child kernel when it is missing.

        ``build`` opens in this kernel's main kernel, ``main/build`` in that
        main kernel. A main kernel lives by an MCP session's lease, so only a
        session opens one (``%%loommux --attach NAME --create``).
        """
        context = None if self.identity is None else self.identity.kernel_id
        return self._handle(self._request("POST", "/api/resources", json={"path": path, "context": context})["resource"])

    def rename(self, kernel: str | Kernel, name: str) -> str:
        """Rename ``kernel`` (a path, an id or a handle) and return the name it received.

        A name another kernel of its scope already has is numbered
        (``name-2``). Renaming this kernel changes which cell headers name it
        from the next cell on.
        """
        resource = self._request("POST", f"/api/resources/{self._id(kernel)}/rename", json={"name": name})["resource"]
        self._learn(resource)
        if isinstance(kernel, Kernel):
            kernel.name = resource["name"]
        return resource["name"]

    def recycle(self, kernel: str | Kernel, *, force: bool = False) -> str:
        """Retire ``kernel`` (a path, an id or a handle); ``force`` also retires it while it runs a cell."""
        return self._request("POST", f"/api/resources/{self._id(kernel)}/recycle", json={"force": force})["message"]

    def recycle_idle(self) -> int:
        """Retire every kernel that runs nothing; how many were retired."""
        return self._request("POST", "/api/resources/recycle-idle")["count"]

    def recycle_all(self) -> int:
        """Retire every kernel, running or not; how many were retired."""
        return self._request("POST", "/api/resources/recycle-all")["count"]

    def _id(self, kernel: str | Kernel) -> str:
        return kernel.id if isinstance(kernel, Kernel) else self[kernel].id

    @property
    def self(self) -> Kernel:
        """The kernel this process is, or was started from."""
        if self.identity is None:
            raise DaemonError("this process is not a Loommux kernel")
        return Kernel(self, self.identity.kernel_id, self.identity.name)

    def notify(self, text: str) -> None:
        """Tell the agent something about this moment, apart from the cell's output.

        Speak during a cell, including from ``pre_run_cell`` and
        ``post_run_cell`` hooks: the notice belongs to that cell, never enters
        its output streams, and is never omitted with long output. The next
        tool result ends with it, once, as one line of that result's
        ``<loommux-notice>`` envelope. A notice is one short alarm with a
        pointer to act on it, cut at 500 characters; a cell keeps its first
        ten. Text spoken while no cell runs belongs to no request and is lost.
        """
        from IPython.core.interactiveshell import InteractiveShell
        from IPython.display import publish_display_data

        # A script started from a kernel inherits its identity, but has no
        # cell to speak in; publishing there would create an IPython shell.
        if self.identity is None or not InteractiveShell.initialized():
            raise DaemonError("notify speaks from a cell of a Loommux kernel")
        publish_display_data({NOTICE_MIME: str(text)})

    def _handle(self, resource: dict[str, Any]) -> Kernel:
        self._learn(resource)
        return Kernel(self, resource["resource_id"], resource["name"], resource)

    def _learn(self, resource: dict[str, Any]) -> None:
        """Take this kernel's current name from any daemon answer describing it.

        The identity decides which cell headers name this kernel, and the
        kernel may have been renamed elsewhere, by its main kernel or through
        the control plane.
        """
        identity = self.identity
        if identity is not None and resource["resource_id"] == identity.kernel_id and resource["name"] != identity.name:
            self.identity = replace(identity, name=resource["name"])

    def close(self) -> None:
        """Close the HTTP connections; a later request opens new ones."""
        if self._http is not None:
            self._http.close()
            self._http = None

    def __repr__(self) -> str:
        where = self.url or "no HTTP control plane (stdio daemon)"
        here = "" if self.identity is None else f" · this kernel {self.identity.name}"
        return f"loommux daemon {where}{here}"

    def _request(self, method: str, path: str, *, json: Any = None, params: dict[str, str] | None = None, timeout: float = _CONTROL_TIMEOUT_SECONDS) -> dict[str, Any]:  # noqa: ANN401
        import httpx

        if self.url is None:
            raise DaemonError("this kernel's daemon runs over stdio and serves no HTTP control plane")
        if self._http is None:
            # The control plane is always on loopback. An inherited ALL_PROXY
            # would otherwise send loopback requests into a proxy.
            self._http = httpx.Client(base_url=self.url, trust_env=False)
        try:
            response = self._http.request(method, path, json=json, params=params, timeout=timeout)
        except httpx.HTTPError as exc:
            raise DaemonError(f"cannot reach the loommux daemon at {self.url}: {exc}") from exc
        try:
            body = response.json()
        except ValueError:
            raise DaemonError(f"the loommux daemon at {self.url} answered {response.status_code} {response.reason_phrase}") from None
        if response.is_error or body.get("ok") is False:
            message = body.get("message") or body.get("result", {}).get("message") or response.reason_phrase
            raise DaemonError(str(message), body.get("status"))
        return body


class Kernel:
    """A handle to one kernel: the eight operations of the MCP tools, aimed at it.

    Each method has the name and parameters of its tool and returns the
    structured result the kernel's session gives. ``execution=None`` selects
    the current execution, then the most recent. Every method asks the daemon
    anew.

    ``snapshot`` is the daemon's description at the moment the handle was
    listed, or ``None`` for a handle made without a listing.
    """

    def __init__(self, daemon: Daemon, kernel_id: str, name: str, snapshot: dict[str, Any] | None = None) -> None:
        self.daemon = daemon
        self.id = kernel_id
        self.name = name
        self.snapshot = snapshot

    def status(self) -> dict[str, Any]:
        """The daemon's current description of this kernel."""
        resource = self.daemon._request("GET", f"/api/resources/{self.id}")["resource"]
        self.daemon._learn(resource)
        return resource

    def run_cell(self, freeform: str, *, wait_seconds: float | None = None, silence_seconds: float | None = None) -> dict[str, Any]:
        """Submit ``freeform`` as one cell and wait as the ``run_cell`` tool does; its state either way.

        The result's ``execution`` is the number the other operations take.
        ``wait_seconds`` and ``silence_seconds`` are ``--wait`` and ``--silence``;
        one left out is the cell header's, whose defaults are the tool's.
        ``wait_seconds=0`` returns at once and leaves the cell running. A
        kernel already running a cell refuses with ``DaemonError`` whose
        ``status`` is ``busy``: cells are never queued.
        """
        body = {"source": freeform} | _given(wait_seconds=wait_seconds, silence_seconds=silence_seconds)
        return self._session("POST", "/executions", json=body, timeout=_header_wait(freeform, wait_seconds) + _CONTROL_TIMEOUT_SECONDS)

    def execution_status(self, execution: int | None = None) -> dict[str, Any]:
        """As the ``execution_status`` tool, for this kernel."""
        return self._session("GET", f"/executions/{_selected(execution)}")

    def read_output(self, execution: int | None = None, stream: str = "combined", line_range: str | None = None, max_chars: int | None = None) -> dict[str, Any]:
        """As the ``read_output`` tool, for this kernel; the lines are ``["text"]``."""
        params = {"stream": stream} | _present(line_range=line_range, max_chars=max_chars)
        return self._session("GET", f"/executions/{_selected(execution)}/output", params=params)

    def search_output(
        self,
        query: str,
        execution: int | None = None,
        stream: str = "combined",
        query_mode: str = "auto",
        context_before: int = 0,
        context_after: int = 0,
        ignore_case: bool = False,
        max_chars: int | None = None,
    ) -> dict[str, Any]:
        """As the ``search_output`` tool, for this kernel; the marked lines are ``["text"]``."""
        params = {"query": query, "stream": stream, "query_mode": query_mode, "context_before": str(context_before), "context_after": str(context_after)}
        params |= {"ignore_case": "true"} if ignore_case else {}
        return self._session("GET", f"/executions/{_selected(execution)}/search", params=params | _present(max_chars=max_chars))

    def wait(self, execution: int | None = None, timeout_seconds: float = DEFAULT_WAIT_SECONDS, silence_seconds: float = DEFAULT_SILENCE_SECONDS) -> dict[str, Any]:
        """As the ``wait`` tool, for this kernel: running out of time or into silence ends this call, never the cell."""
        body = {"timeout_seconds": timeout_seconds, "silence_seconds": silence_seconds}
        return self._session("POST", f"/executions/{_selected(execution)}/wait", json=body, timeout=timeout_seconds + _CONTROL_TIMEOUT_SECONDS)

    def interrupt(self) -> dict[str, Any]:
        """As the ``interrupt`` tool, for this kernel: the cell it is running now."""
        return self.daemon._request("POST", f"/api/resources/{self.id}/interrupt")["result"]

    def restart(self) -> dict[str, Any]:
        """Restart the kernel.

        Restarting the kernel that calls this ends the calling cell with it.
        """
        return self.daemon._request("POST", f"/api/resources/{self.id}/restart")["result"]

    def follow(self, execution: int, offset: int, wait: float = 0.5) -> dict[str, Any]:
        """Combined output of ``execution`` after ``offset`` characters, with ``status`` and the next ``offset``.

        Answers as soon as new text exists or the execution ends, within
        ``wait`` seconds, so a loop of calls receives output as it arrives:
        what a tool result does for an agent, for a program.
        """
        return self._session("GET", f"/executions/{execution}/combined", params={"since": str(offset), "wait": str(wait)})

    def _session(self, method: str, path: str, **options: Any) -> dict[str, Any]:  # noqa: ANN401
        return self.daemon._request(method, f"/api/resources/{self.id}{path}", **options)["result"]

    def __repr__(self) -> str:
        return f"<loommux kernel {self.name} {self.id[:8]}>"


class Kernels(list[Kernel]):
    """Listed kernels; displays as one row per kernel."""

    def __repr__(self) -> str:
        if not self:
            return "no kernels"
        rows = [("name", "id", "kernel", "busy", "idle", "pid", "executions", "leases", "kept", "scope", "home")]
        now = datetime.now(UTC)
        for kernel in self:
            snapshot = kernel.snapshot or {}
            retention = snapshot.get("retention_remaining_seconds")
            idle_since = snapshot.get("idle_since")
            rows.append(
                (
                    kernel.name,
                    kernel.id[:8],
                    str(snapshot.get("kernel_state", "")),
                    "busy" if snapshot.get("busy") else "idle",
                    "-" if idle_since is None else _duration((now - datetime.fromisoformat(idle_since)).total_seconds()),
                    str(snapshot.get("kernel_pid") or "-"),
                    str(snapshot.get("execution_count", "")),
                    str(snapshot.get("lease_count", "")),
                    "-" if retention is None else _duration(retention),
                    str(snapshot.get("scope", "")),
                    str(snapshot.get("home") or "-"),
                )
            )
        widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
        return "\n".join("  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip() for row in rows)


def _duration(seconds: float) -> str:
    if seconds >= 3600:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 60:.0f}m" if seconds >= 60 else f"{max(seconds, 0):.0f}s"


def _selected(execution: int | None) -> str:
    # The tools' None, current then most recent, is "latest" in a path.
    return "latest" if execution is None else str(execution)


def _present(**values: object) -> dict[str, str]:
    return {name: str(value) for name, value in values.items() if value is not None}


def _given(**values: float | None) -> dict[str, float]:
    # A JSON body keeps the number a number; _present is for query strings.
    return {name: value for name, value in values.items() if value is not None}


def _header_wait(freeform: str, wait_seconds: float | None) -> float:
    """How long the daemon may hold this submission, for the HTTP timeout."""
    if wait_seconds is not None:
        return wait_seconds
    try:
        return read_cell_header(freeform).wait_seconds
    except CellHeaderError:
        # The daemon refuses the header before it waits at all.
        return DEFAULT_WAIT_SECONDS
