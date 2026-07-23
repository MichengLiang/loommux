from __future__ import annotations

import pytest

import loommux.mcp_ipython_server as server
from loommux.mcp_result_policy import ResultMode
from loommux.server_entrypoints import run_entrypoint


class RecordingMCP:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


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


@pytest.mark.parametrize("argv", [["--server", "--port", "0"], ["--server", "--port", "65536"], ["--server", "--path", "tools"], ["--transport", "stdio"], ["--result-mode", "content"]])
def test_invalid_or_removed_options_are_rejected_before_starting(argv: list[str]) -> None:
    with pytest.raises(SystemExit, match="2"):
        run_entrypoint(lambda _mode: RecordingMCP(), argv=argv)
