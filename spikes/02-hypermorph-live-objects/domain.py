"""Live objects shaped like loommux's, small enough to measure.

Two Execution shapes answer the first question of the spike: whether the
output body can be ordinary state, or must live outside the Runtime with only
its summary committed. The Resource carries a growing summary table so a probe
can see whether the size of the rest of an object's state taxes each commit.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Literal, TypedDict

from hypermorph import LiveObject, UpdateMode, modifies, state_field

type Status = Literal["running", "ok", "error", "interrupted"]
TERMINAL = frozenset({"ok", "error", "interrupted"})


class TextExecution(LiveObject):
    """The body is state, so every commit and every observer carries it."""

    status: Status = "running"
    output: Annotated[str, UpdateMode("append")] = ""

    def poke(self) -> None:
        """An exclusive command that changes nothing: stands in for interrupt."""

    def _on_chunk(self, chunk: str) -> None:
        self.state.append("output", chunk)

    def _done(self) -> None:
        self.status = "ok"


class CountedExecution(LiveObject):
    """Only the summary is state; the body goes to a log outside the Runtime (LineLog's place)."""

    status: Status = "running"
    lines: int = 0
    chars: int = 0

    def __init__(self) -> None:
        self._log: list[str] = []

    def poke(self) -> None:
        """An exclusive command that changes nothing: stands in for interrupt."""

    @modifies()
    def tail(self, chars: int = 200) -> str:
        """Read-only: the last characters of the body, sharing the lock with snapshots."""
        return "".join(self._log)[-chars:]

    def _on_chunk(self, chunk: str) -> None:
        self._log.append(chunk)
        self.lines += chunk.count("\n")
        self.chars += len(chunk)

    def _done(self) -> None:
        # The source ends at the kernel's idle; an error seen before it already decided the status.
        if self.status == "running":
            self.status = "ok"

    def _finish(self, status: Status) -> None:
        self.status = status


class Summary(TypedDict):
    status: str
    chars: int


class Resource(LiveObject):
    """A kernel resource whose state also lists every execution it has run."""

    busy: bool = False
    executions: Annotated[dict[str, Summary], UpdateMode("mapping")] = state_field(default_factory=dict)

    def _fill(self, count: int) -> None:
        for index in range(count):
            self.state.set(("executions", str(index)), Summary(status="ok", chars=index))

    def _tick(self, _: object) -> None:
        self.busy = not self.busy


class Daemon(LiveObject):
    """The resource directory as state: name -> lifecycle."""

    resources: Annotated[dict[str, str], UpdateMode("mapping")] = state_field(default_factory=dict)

    def __init__(self, open_resource: Callable[[str], None]) -> None:
        self._open_resource = open_resource

    def open(self, name: str) -> str:
        """Open a kernel resource and list it in the directory."""
        self._open_resource(name)
        self.state.set(("resources", name), "running")
        return name

    def _forget(self, name: str) -> None:
        self.state.remove(("resources", name))
