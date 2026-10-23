"""Control a Loommux daemon from Python: list, address, and manage its kernels.

Every Loommux kernel holds one :class:`Daemon` as ``loommux``, bound to the
daemon that started it::

    loommux.kernels()               # every kernel, one row each
    loommux.self                    # this kernel
    build = loommux["build"]        # this home's child kernel, opened on first use
    job = build.run("make()", wait=5)
    job.wait(600); job.output()
    loommux.notify("⚠ memory is low")  # to the agent, apart from this cell's output

Inside a kernel the client sees its home: the main kernel an MCP session works
in and the child kernels opened from it. Kernels of other homes stay out of its
listing and its names, so two homes may each have their own ``build``.
Outside a kernel, ``Daemon()`` connects to ``LOOMMUX_CONTROL_URL`` or to
``http://127.0.0.1:8801`` and acts for the person running loommux: it lists
every kernel. The client speaks the daemon's HTTP control plane, so a mistake
made through it stays in the calling process and can never stop the daemon's
event loop.

A kernel's identity is its environment, which its child processes inherit:
a script started from a kernel acts as that kernel, holding what it opens on
that kernel's behalf.
"""

from __future__ import annotations

import os
from dataclasses import replace
from typing import TYPE_CHECKING, Any

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
    """A client of one Loommux daemon's HTTP control plane."""

    def __init__(self, url: str | None = None) -> None:
        self.identity = KernelIdentity.from_environ()
        if url is not None:
            self.url: str | None = url.rstrip("/")
        elif self.identity is not None:
            self.url = self.identity.control_url
        else:
            self.url = os.environ.get(CONTROL_URL_ENV) or DEFAULT_CONTROL_URL
        self._http: httpx.Client | None = None

    def kernels(self) -> Kernels:
        """The kernels of this kernel's home, or outside a kernel every kernel; oldest first."""
        params = None if self.identity is None else {"home": self.identity.kernel_id}
        resources = self._request("GET", "/api/resources", params=params)["resources"]
        return Kernels(self._handle(resource) for resource in resources)

    def __getitem__(self, name: str) -> Kernel:
        """The kernel with this name or id.

        Inside a kernel the name is this home's child kernel, opened on first
        use and retired with the home's main kernel; this kernel's and the
        main kernel's own names resolve to them. Outside a kernel nothing could
        hold a new kernel, so only a main kernel by name, or any kernel by id,
        resolves.
        """
        holder = None if self.identity is None else self.identity.kernel_id
        resource = self._request("POST", "/api/resources/ensure", json={"name": name, "holder": holder})["resource"]
        return self._handle(resource)

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
        tool result ends with it, once. A notice is one short alarm with a
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
    """A handle to one kernel resource; every method asks the daemon anew.

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

    def run(self, code: str, *, wait: float = 10.0) -> Job:
        """Submit ``code`` as one cell and wait up to ``wait`` seconds.

        Returns while the cell may still run; the job follows it. A kernel
        that is already running a cell refuses with ``DaemonError`` whose
        ``status`` is ``busy``: cells are never queued.
        """
        result = self.daemon._request("POST", f"/api/resources/{self.id}/executions", json={"source": code, "wait_seconds": wait}, timeout=wait + _CONTROL_TIMEOUT_SECONDS)["result"]
        return Job(self, result["execution"], result)

    def interrupt(self) -> dict[str, Any]:
        return self.daemon._request("POST", f"/api/resources/{self.id}/interrupt")["result"]

    def restart(self) -> dict[str, Any]:
        """Replace the kernel process; its execution records go with it and the next cell is 1.

        Restarting the kernel that calls this ends the calling cell with it.
        """
        return self.daemon._request("POST", f"/api/resources/{self.id}/restart")["result"]

    def recycle(self, *, force: bool = False) -> str:
        """Retire the kernel resource; ``force`` also retires it while busy."""
        return self.daemon._request("POST", f"/api/resources/{self.id}/recycle", json={"force": force})["message"]

    def rename(self, name: str) -> str:
        """Rename the kernel and return the name it received.

        A name another kernel of its scope already has is numbered
        (``name-2``). Renaming the kernel that runs this takes effect for the
        next cell header at once.
        """
        resource = self.daemon._request("POST", f"/api/resources/{self.id}/rename", json={"name": name})["resource"]
        self.daemon._learn(resource)
        self.name = resource["name"]
        return self.name

    def __repr__(self) -> str:
        return f"<loommux kernel {self.name} {self.id[:8]}>"


class Job:
    """One execution of a kernel, followed through the daemon.

    ``last`` is the most recent state the daemon reported for it.
    """

    def __init__(self, kernel: Kernel, execution: int, last: dict[str, Any]) -> None:
        self.kernel = kernel
        self.execution = execution
        self.last = last

    @property
    def done(self) -> bool:
        return self.last.get("status") != "running"

    def status(self) -> dict[str, Any]:
        return self._update("GET", f"/executions/{self.execution}")

    def wait(self, timeout: float = 30) -> dict[str, Any]:
        """Wait up to ``timeout`` seconds for the execution to end; its state either way."""
        return self._update("POST", f"/executions/{self.execution}/wait", json={"timeout_seconds": timeout}, timeout=timeout + _CONTROL_TIMEOUT_SECONDS)

    def output(self, stream: str = "combined", lines: str | None = None) -> str:
        """Text of one output stream; ``lines`` is a ``start:stop`` range as in ``read_output``."""
        params = {"stream": stream} | ({} if lines is None else {"lines": lines})
        return self.kernel.daemon._request("GET", f"/api/resources/{self.kernel.id}/executions/{self.execution}/output", params=params)["result"]["text"]

    def follow(self, offset: int, wait: float = 0.5) -> dict[str, Any]:
        """Combined output after ``offset`` characters, with ``status`` and the next ``offset``.

        Answers as soon as new text exists or the execution ends, within
        ``wait`` seconds, so a loop of calls relays output as it arrives.
        """
        params = {"since": str(offset), "wait": str(wait)}
        self.last = self.kernel.daemon._request("GET", f"/api/resources/{self.kernel.id}/executions/{self.execution}/combined", params=params)["result"]
        return self.last

    def interrupt(self) -> dict[str, Any]:
        return self.kernel.interrupt()

    def _update(self, method: str, path: str, **options: Any) -> dict[str, Any]:  # noqa: ANN401
        self.last = self.kernel.daemon._request(method, f"/api/resources/{self.kernel.id}{path}", **options)["result"]
        return self.last

    def __repr__(self) -> str:
        return f"<loommux job {self.kernel.name} #{self.execution} {self.last.get('status')}>"


class Kernels(list[Kernel]):
    """Listed kernels; displays as one row per kernel."""

    def __repr__(self) -> str:
        if not self:
            return "no kernels"
        rows = [("name", "id", "kernel", "busy", "pid", "executions", "leases", "kept", "scope", "home")]
        for kernel in self:
            snapshot = kernel.snapshot or {}
            retention = snapshot.get("retention_remaining_seconds")
            rows.append(
                (
                    kernel.name,
                    kernel.id[:8],
                    str(snapshot.get("kernel_state", "")),
                    "busy" if snapshot.get("busy") else "idle",
                    str(snapshot.get("kernel_pid") or "-"),
                    str(snapshot.get("execution_count", "")),
                    str(snapshot.get("lease_count", "")),
                    "-" if retention is None else f"{retention / 3600:.1f}h" if retention >= 3600 else f"{retention:g}s",
                    str(snapshot.get("scope", "")),
                    str(snapshot.get("home") or "-"),
                )
            )
        widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
        return "\n".join("  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip() for row in rows)
