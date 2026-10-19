from __future__ import annotations

import faulthandler
import re
import subprocess
import sys

import pytest

import loommux.mcp.server as server
from loommux.mcp.entrypoints import run_entrypoint
from loommux.mcp.result import ResultMode


class RecordingMCP:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


@pytest.fixture(autouse=True)
def _no_real_stack_dump_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    # run_entrypoint registers SIGUSR1 for the life of its process; in-process
    # tests must not leave that handler writing into pytest's captured stderr.
    # The subprocess test below exercises the real registration.
    monkeypatch.setattr(faulthandler, "register", lambda *_args, **_kwargs: None)


def test_default_entrypoint_uses_content_stdio_transport() -> None:
    mcp = RecordingMCP()
    created: list[ResultMode] = []

    run_entrypoint(lambda mode: created.append(mode) or mcp, argv=[])

    assert created == ["content"]
    assert mcp.calls == [{"transport": "stdio"}]


def test_structured_mode_is_explicit_and_works_with_http_server() -> None:
    mcp = RecordingMCP()
    created: list[ResultMode] = []

    run_entrypoint(
        lambda mode: created.append(mode) or mcp,
        argv=["--server", "--result-mode", "structured", "--host", "127.0.0.1", "--port", "9137", "--path", "/tools"],
    )

    assert created == ["structured"]
    assert mcp.calls == [{"transport": "streamable-http", "host": "127.0.0.1", "port": 9137, "path": "/tools"}]


def test_server_flag_selects_http_without_enabling_structured_results() -> None:
    mcp = RecordingMCP()
    created: list[ResultMode] = []

    run_entrypoint(lambda mode: created.append(mode) or mcp, argv=["--server"])

    assert created == ["content"]
    assert mcp.calls == [{"transport": "streamable-http", "host": "127.0.0.1", "port": 8801, "path": "/mcp"}]


def test_main_forwards_the_single_entrypoint_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def run_entrypoint(*args: object, **kwargs: object) -> None:
        captured["args"] = args
        captured.update(kwargs)

    monkeypatch.setattr(server, "run_entrypoint", run_entrypoint)

    server.main(["--server", "--port", "9100"])

    assert len(captured["args"]) == 1
    assert captured["argv"] == ["--server", "--port", "9100"]


@pytest.mark.parametrize("argv", [["--server", "--port", "0"], ["--server", "--port", "65536"], ["--server", "--path", "tools"], ["--server", "--path", "/"], ["--server", "--path", "/api/private"], ["--transport", "stdio"], ["--result-mode", "content"]])
def test_invalid_or_removed_options_are_rejected_before_starting(argv: list[str]) -> None:
    with pytest.raises(SystemExit, match="2"):
        run_entrypoint(lambda _mode: RecordingMCP(), argv=argv)


@pytest.mark.skipif(sys.platform == "win32", reason="faulthandler.register is POSIX-only")
def test_sigusr1_prints_every_thread_stack_while_the_server_runs() -> None:
    script = (
        "import os, signal, threading, time\n"
        "from loommux.mcp.entrypoints import run_entrypoint\n"
        "class BlockedMCP:\n"
        "    def run(self, **kwargs):\n"
        "        threading.Thread(target=time.sleep, args=(5,), daemon=True).start()\n"
        "        os.kill(os.getpid(), signal.SIGUSR1)\n"
        "        time.sleep(0.2)\n"
        "run_entrypoint(lambda mode: BlockedMCP(), argv=['--server'])\n"
    )

    completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=10)

    assert completed.returncode == 0
    assert len(re.findall(r"[Tt]hread 0x[0-9a-f]+ \(most recent call first\)", completed.stderr)) >= 2
    assert "in run" in completed.stderr
