"""Interpret the ``%%loommux`` cell header inside a Loommux kernel.

Every Loommux kernel loads this IPython extension at start. A header that
names this kernel, or names none, is dissolved while IPython transforms the
cell, so the body runs as the cell itself. That cannot be a cell magic: a cell
magic is synchronous, its only way to run a body is a nested ``run_cell``,
and that cannot run top-level ``await`` while the kernel's loop is running the
outer cell.

A header naming another kernel stays for IPython's cell-magic transform and
reaches ``%%loommux``, which forwards the body there.

``--attach`` is the daemon's: by the time a cell reaches a kernel, the daemon
has already moved the submitting session to the kernel it names. A kernel
therefore treats ``--attach`` naming itself as its own header, and any other
``--attach`` as a mistake.
"""

from __future__ import annotations

import functools
import sys
import time
from collections.abc import Callable, Collection
from typing import Literal

from IPython.core.error import UsageError
from IPython.core.interactiveshell import InteractiveShell

from loommux.cell_header import HEADER_HELP, HEADER_MAGIC, CellHeader, CellHeaderError, header_arguments, parse_header_arguments
from loommux.daemon import Daemon, DaemonError, Job
from loommux.identity import KernelIdentity

# How long an interrupted forwarding cell keeps relaying while the target
# reacts to the interrupt it passed on.
INTERRUPT_RELAY_SECONDS = 10.0


def remove_own_header(lines: list[str], own_names: Collection[str] = ()) -> list[str]:
    """Dissolve a first-line header that names this kernel or no kernel.

    IPython may transform one cell more than once, for example to check that
    it is complete, so this stays a pure function of its arguments. An invalid
    header is left in place for the cell magic, which reports it.

    A cell has one header. One followed by another ``%%loommux`` line is left
    in place too, and the cell magic reports it: the daemon reads only the
    first line, so a second header's options would have no reader.

    The header becomes an empty line, so traceback line numbers count lines of
    the cell as it was written. A body that is itself a cell magic, such as
    ``%%bash``, must start the cell for IPython to recognize it; its line
    numbers belong to its own interpreter.
    """
    if not lines or (arguments := header_arguments(lines[0])) is None:
        return lines
    try:
        header = parse_header_arguments(arguments)
    except CellHeaderError:
        return lines
    target = header.attach if header.attach is not None else header.kernel
    if target is not None and target not in own_names:
        return lines
    body = lines[1:]
    if body and header_arguments(body[0]) is not None:
        return lines
    if body and body[0].startswith("%%"):
        return body
    return ["\n", *body]


class ForwardedCellError(Exception):
    """A forwarded cell did not complete; its own traceback was already relayed."""

    def __init__(self, kernel: str, kind: str, detail: str) -> None:
        super().__init__(f"{kernel}: {kind}: {detail}" if detail else f"{kernel}: {kind}")

    def _render_traceback_(self) -> list[str]:
        # IPython displays this instead of a traceback through the magic's
        # frames, which say nothing about the forwarded cell.
        return [f"ForwardedCellError: {self}"]


class ForwardedInterrupt(KeyboardInterrupt):
    """This cell was interrupted and passed the interrupt to the kernel it forwarded to."""

    def __init__(self, kernel: str) -> None:
        super().__init__(f"passed on to {kernel}; its traceback is above")

    def _render_traceback_(self) -> list[str]:
        # The caught interrupt's traceback runs through the HTTP client's frames
        # wherever the signal landed, and IPython never hides the raising frame.
        return [f"KeyboardInterrupt: {self}"]


# The kernel reports an error by its class name, and an interrupted execution
# is recognized by the name KeyboardInterrupt; a forwarded interrupt is one.
ForwardedInterrupt.__name__ = "KeyboardInterrupt"


def forward(daemon: Daemon, header: CellHeader, body: str, write: Callable[[str], object]) -> None:
    """Run ``body`` in the kernel the header names and relay its output.

    The forwarding cell ends as the forwarded one did: an error there raises
    here, and an interrupt here is passed down before it is raised.
    """
    __tracebackhide__ = True
    assert header.kernel is not None
    try:
        job = daemon[header.kernel].run(body, wait=0.05)
    except DaemonError as exc:
        raise ForwardedCellError(header.kernel, exc.status or "DaemonError", str(exc)) from None
    try:
        _relay(job, write, 0, deadline=None)
    except KeyboardInterrupt:
        raise ForwardedInterrupt(header.kernel) from None
    except DaemonError as exc:
        # The target was retired or restarted while its cell ran here.
        raise ForwardedCellError(header.kernel, exc.status or "DaemonError", str(exc)) from None
    _raise_for(header.kernel, job.last)


def _relay(job: Job, write: Callable[[str], object], offset: int, deadline: float | None) -> int:
    __tracebackhide__ = True
    try:
        while deadline is None or time.monotonic() < deadline:
            state = job.follow(offset)
            if state["text"]:
                write(state["text"])
            offset = state["offset"]
            if state["status"] != "running":
                break
    except KeyboardInterrupt:
        if deadline is not None:
            raise
        job.interrupt()
        _relay(job, write, offset, deadline=time.monotonic() + INTERRUPT_RELAY_SECONDS)
        raise
    return offset


def _raise_for(kernel: str, state: dict[str, object]) -> None:
    status = state["status"]
    if status == "completed":
        return
    error = state.get("error")
    if isinstance(error, dict) and error.get("ename"):
        raise ForwardedCellError(kernel, str(error["ename"]), str(error.get("evalue") or ""))
    raise ForwardedCellError(kernel, str(status), "")


def _header_magic(line: str, cell: str, *, daemon: Daemon, own_names: Collection[str]) -> None:
    __tracebackhide__ = True
    try:
        header = parse_header_arguments(line)
    except CellHeaderError as exc:
        raise UsageError(f"invalid %%{HEADER_MAGIC} header: {exc}") from None
    if header.attach is not None and header.attach not in own_names:
        # Reached by a cell forwarded here or submitted through the control
        # plane: no MCP session submitted it, so nothing can be moved.
        raise UsageError(
            f"--attach {header.attach} moves an MCP session to another main kernel, and loommux reads it only on the first line "
            "of a cell a session submits, and this cell reached this kernel another way. Use --kernel to run it elsewhere"
        )
    if header.kernel is None or header.kernel in own_names:
        # Reached by a header followed by another one, which the transformer
        # leaves in place, or by a direct run_cell_magic call. Either way the
        # body cannot run here: a cell magic could only run it through a
        # nested run_cell, which cannot run top-level await.
        raise UsageError(
            f"a %%{HEADER_MAGIC} header naming this kernel must be the cell's only header, on its first line; "
            f"write every option there, as in %%{HEADER_MAGIC} --kernel build --wait 60"
        )
    forward(daemon, header, cell, sys.stdout.write)


def _misplaced_header(line: str) -> None:
    raise UsageError(f"%%{HEADER_MAGIC} must be the first line of the cell")


def _single_percent_header(line: str) -> None:
    raise UsageError(f"write the header as %%{HEADER_MAGIC}, with two percent signs, on the first line of the cell")


_MAGICS: tuple[tuple[Literal["cell", "line"], str], ...] = (("cell", HEADER_MAGIC), ("line", f"%{HEADER_MAGIC}"), ("line", HEADER_MAGIC))
_installed: dict[int, tuple[Callable[[list[str]], list[str]], Daemon]] = {}


def load_ipython_extension(ip: InteractiveShell) -> None:
    if id(ip) in _installed:
        return
    identity = KernelIdentity.from_environ()
    own_names = frozenset() if identity is None else identity.names
    daemon = Daemon()
    transformer = functools.partial(remove_own_header, own_names=own_names)
    _installed[id(ip)] = (transformer, daemon)
    ip.input_transformers_cleanup.append(transformer)
    # Hidden from %who: it belongs to the kernel, not to the author's work.
    ip.push({"loommux": daemon}, interactive=False)
    magic = functools.partial(_header_magic, daemon=daemon, own_names=own_names)
    functools.update_wrapper(magic, _header_magic)
    magic.__doc__ = HEADER_HELP
    # IPython reads ``%%loommux`` on a later line as a line magic whose name
    # keeps one percent sign, so the second name catches exactly that mistake.
    for (kind, name), function in zip(_MAGICS, (magic, _misplaced_header, _single_percent_header), strict=True):
        ip.magics_manager.register_function(function, kind, name)


def unload_ipython_extension(ip: InteractiveShell) -> None:
    installed = _installed.pop(id(ip), None)
    if installed is None:
        return
    transformer, daemon = installed
    if transformer in ip.input_transformers_cleanup:
        ip.input_transformers_cleanup.remove(transformer)
    for kind, name in _MAGICS:
        ip.magics_manager.magics[kind].pop(name, None)
    # Only while the name still holds this client; the author may have rebound it.
    ip.drop_by_id({"loommux": daemon})
    daemon.close()
