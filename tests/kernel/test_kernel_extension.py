from __future__ import annotations

from typing import Any

import pytest
from IPython.core.error import UsageError

from loommux import kernel_extension
from loommux.cell_header import HEADER_HELP, CellHeader
from loommux.daemon import DaemonError
from loommux.identity import KernelIdentity
from loommux.kernel_extension import ForwardedCellError, ForwardedInterrupt, forward, remove_own_header


@pytest.mark.parametrize(
    ("lines", "transformed"),
    [
        (["x = 1\n"], ["x = 1\n"]),
        ([], []),
        (["%%loommux --wait 2\n", "x = 1\n"], ["\n", "x = 1\n"]),
        (["%%loommux\n", "%%loommux --full-output\n", "x\n"], ["%%loommux\n", "%%loommux --full-output\n", "x\n"]),
        (["%%loommux --wait 2\n", "%%bash\n", "echo\n"], ["%%bash\n", "echo\n"]),
        (["%%loommux --wait 2\n"], ["\n"]),
        (["%%loommux --bogus\n", "x\n"], ["%%loommux --bogus\n", "x\n"]),
        (["%%loommux\n", "%%loommux --bogus\n", "x\n"], ["%%loommux\n", "%%loommux --bogus\n", "x\n"]),
        (["%%loommux?\n"], ["%%loommux?\n"]),
        (["%%loommux --kernel me --wait 2\n", "x\n"], ["\n", "x\n"]),
        (["%%loommux --kernel me-id\n", "x\n"], ["\n", "x\n"]),
        (["%%loommux --kernel build\n", "x\n"], ["%%loommux --kernel build\n", "x\n"]),
        (["%%loommux --wait 2\n", "%%loommux --kernel build\n", "x\n"], ["%%loommux --wait 2\n", "%%loommux --kernel build\n", "x\n"]),
        (["%%loommux --kernel build\n", "%%loommux --full-output\n", "x\n"], ["%%loommux --kernel build\n", "%%loommux --full-output\n", "x\n"]),
        # The daemon already moved the session here, so attaching to this
        # kernel is this kernel's header; any other is left for the magic.
        (["%%loommux --attach me --wait 5\n", "x\n"], ["\n", "x\n"]),
        (["%%loommux --attach elsewhere\n", "x\n"], ["%%loommux --attach elsewhere\n", "x\n"]),
    ],
)
def test_headers_naming_this_kernel_dissolve_and_keep_line_numbers(lines: list[str], transformed: list[str]) -> None:
    assert remove_own_header(lines, own_names={"me", "me-id"}) == transformed


def test_transformer_is_pure_so_ipython_may_apply_it_twice() -> None:
    lines = ["%%loommux --wait 2\n", "x = 1\n"]

    remove_own_header(lines)

    assert lines == ["%%loommux --wait 2\n", "x = 1\n"]


class _Shell:
    def __init__(self) -> None:
        self.input_transformers_cleanup: list[Any] = []
        self.magics: dict[str, dict[str, Any]] = {"line": {}, "cell": {}}
        self.namespace: dict[str, Any] = {}
        self.magics_manager = self

    def push(self, variables: dict[str, Any], interactive: bool = True) -> None:
        assert interactive is False
        self.namespace.update(variables)

    def drop_by_id(self, variables: dict[str, Any]) -> None:
        for name, value in variables.items():
            if self.namespace.get(name) is value:
                del self.namespace[name]

    def register_function(self, function: Any, kind: str, name: str) -> None:
        self.magics[kind][name] = function


def test_loading_twice_installs_once_and_unloading_removes_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOOMMUX_KERNEL_ID", "abc123")
    monkeypatch.setenv("LOOMMUX_KERNEL_NAME", "lab")
    shell: Any = _Shell()

    kernel_extension.load_ipython_extension(shell)
    kernel_extension.load_ipython_extension(shell)

    [transformer] = shell.input_transformers_cleanup
    assert transformer(["%%loommux --kernel lab\n", "x\n"]) == ["\n", "x\n"]
    assert transformer(["%%loommux --kernel abc123\n", "x\n"]) == ["\n", "x\n"]
    assert {kind: set(names) for kind, names in shell.magics.items()} == {"cell": {"loommux"}, "line": {"%loommux", "loommux"}}
    # %%loommux? shows the grammar itself, the one text both readers parse by.
    assert shell.magics["cell"]["loommux"].__doc__ == HEADER_HELP
    assert type(shell.namespace["loommux"]).__name__ == "Daemon"

    kernel_extension.unload_ipython_extension(shell)
    kernel_extension.unload_ipython_extension(shell)

    assert shell.input_transformers_cleanup == []
    assert shell.magics == {"line": {}, "cell": {}}
    assert shell.namespace == {}


def test_a_kernel_reads_its_new_name_as_its_own_from_the_next_cell(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOOMMUX_KERNEL_ID", "abc123")
    monkeypatch.setenv("LOOMMUX_KERNEL_NAME", "lab")
    shell: Any = _Shell()
    kernel_extension.load_ipython_extension(shell)
    [transformer] = shell.input_transformers_cleanup

    client = shell.namespace["loommux"]
    client._learn({"resource_id": "abc123", "name": "lab"})
    assert transformer(["%%loommux --kernel lab\n", "x\n"]) == ["\n", "x\n"]
    assert transformer(["%%loommux --attach lab --create\n", "x\n"]) == ["\n", "x\n"]
    client._learn({"resource_id": "abc123", "name": "report"})

    assert transformer(["%%loommux --kernel report\n", "x\n"]) == ["\n", "x\n"]
    # The old name is no longer this kernel's.
    assert transformer(["%%loommux --kernel lab\n", "x\n"]) == ["%%loommux --kernel lab\n", "x\n"]
    client._learn({"resource_id": "someone-else", "name": "other"})
    assert client.self.name == "report"
    kernel_extension.unload_ipython_extension(shell)


def test_a_forward_that_resolves_to_this_kernel_asks_for_the_cell_again() -> None:
    daemon = _Daemon(_Job([]), identity=KernelIdentity("me-id", "old", None), target_id="me-id")

    with pytest.raises(UsageError, match="renamed is this kernel, renamed from elsewhere"):
        forward(daemon, CellHeader(kernel="renamed"), "body", print)

    assert daemon.bodies == []


def test_attaching_from_inside_a_kernel_is_a_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOOMMUX_KERNEL_ID", "abc123")
    monkeypatch.setenv("LOOMMUX_KERNEL_NAME", "build")
    shell: Any = _Shell()
    kernel_extension.load_ipython_extension(shell)

    with pytest.raises(UsageError, match="--attach lab moves an MCP session to another main kernel"):
        shell.magics["cell"]["loommux"]("--attach lab", "x\n")
    kernel_extension.unload_ipython_extension(shell)


def test_unloading_keeps_a_loommux_name_the_author_rebound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOOMMUX_KERNEL_ID", raising=False)
    shell: Any = _Shell()
    kernel_extension.load_ipython_extension(shell)
    shell.namespace["loommux"] = "mine"

    kernel_extension.unload_ipython_extension(shell)

    assert shell.namespace == {"loommux": "mine"}


class _Job:
    def __init__(self, states: list[Any]) -> None:
        self.states = states
        self.last: dict[str, Any] = {"status": "running"}
        self.interrupted = 0

    def follow(self, offset: int) -> dict[str, Any]:
        state = self.states.pop(0)
        if isinstance(state, BaseException):
            raise state
        self.last = state
        return state

    def interrupt(self) -> None:
        self.interrupted += 1


class _Daemon:
    def __init__(self, job: _Job | None = None, refusal: DaemonError | None = None, identity: KernelIdentity | None = None, target_id: str = "target-id") -> None:
        self.job, self.refusal, self.bodies = job, refusal, []
        self.identity, self.target_id = identity, target_id

    def __getitem__(self, name: str) -> Any:
        daemon = self

        class Target:
            id = daemon.target_id

            def __init__(self) -> None:
                self.name = name

            def run(self, body: str, *, wait: float) -> _Job:
                if daemon.refusal is not None:
                    raise daemon.refusal
                daemon.bodies.append(body)
                assert daemon.job is not None
                return daemon.job

        return Target()


def state(status: str, text: str = "", offset: int = 0, error: dict[str, str] | None = None) -> dict[str, Any]:
    return {"status": status, "text": text, "offset": offset, "error": error}


def run_forward(daemon: Any) -> str:
    written: list[str] = []
    forward(daemon, CellHeader(kernel="build"), "body", written.append)
    return "".join(written)


def test_forward_relays_output_until_the_target_completes() -> None:
    job = _Job([state("running", "a\n", 2), state("running", "", 2), state("completed", "b\n", 4)])
    daemon = _Daemon(job)

    assert run_forward(daemon) == "a\nb\n"
    assert daemon.bodies == ["body"]


@pytest.mark.parametrize(
    ("final", "message"),
    [
        (state("error", "Traceback\n", 10, {"ename": "NameError", "evalue": "name 'x' is not defined"}), "build: NameError: name 'x' is not defined"),
        (state("killed", error={"ename": "KernelProcessExited", "evalue": ""}), "build: KernelProcessExited"),
        (state("interrupted"), "build: interrupted"),
    ],
)
def test_forward_ends_as_the_target_ended_with_a_one_line_error(final: dict[str, Any], message: str) -> None:
    with pytest.raises(ForwardedCellError) as raised:
        run_forward(_Daemon(_Job([final])))

    assert str(raised.value) == message
    assert raised.value._render_traceback_() == [f"ForwardedCellError: {message}"]


def test_a_refused_forward_names_the_reason() -> None:
    with pytest.raises(ForwardedCellError, match="build: busy: kernel is already executing code"):
        run_forward(_Daemon(refusal=DaemonError("kernel is already executing code", "busy")))
    with pytest.raises(ForwardedCellError, match="build: DaemonError: no control plane"):
        run_forward(_Daemon(refusal=DaemonError("no control plane")))


def test_a_target_retired_while_relaying_ends_the_cell_in_one_line() -> None:
    job = _Job([state("running", "partial\n", 8), DaemonError("resource was not found")])

    with pytest.raises(ForwardedCellError, match="^build: DaemonError: resource was not found$"):
        run_forward(_Daemon(job))


def test_an_interrupt_is_passed_to_the_target_and_its_last_output_relayed() -> None:
    job = _Job([state("running", "working\n", 8), KeyboardInterrupt(), state("interrupted", "KeyboardInterrupt\n", 26)])

    with pytest.raises(ForwardedInterrupt) as raised:
        run_forward(_Daemon(job))

    assert job.interrupted == 1
    assert raised.value._render_traceback_() == ["KeyboardInterrupt: passed on to build; its traceback is above"]
    assert type(raised.value).__name__ == "KeyboardInterrupt"


def test_an_interrupt_while_draining_is_not_passed_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _Job([KeyboardInterrupt(), KeyboardInterrupt()])

    with pytest.raises(KeyboardInterrupt):
        run_forward(_Daemon(job))

    assert job.interrupted == 1


def test_draining_after_an_interrupt_stops_at_its_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kernel_extension, "INTERRUPT_RELAY_SECONDS", 0.0)
    job = _Job([KeyboardInterrupt()])

    with pytest.raises(KeyboardInterrupt):
        run_forward(_Daemon(job))

    assert job.interrupted == 1
