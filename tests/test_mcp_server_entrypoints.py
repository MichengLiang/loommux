from __future__ import annotations

import pytest

import loommux.mcp_ipython_server as standard_server
from loommux.mcp_result_policy import ResultChannelPolicy
from loommux.server_entrypoints import run_entrypoint


class RecordingMCP:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


def test_default_entrypoint_uses_its_default_policy_and_stdio_transport() -> None:
    mcp = RecordingMCP()
    created: list[ResultChannelPolicy] = []

    run_entrypoint(lambda policy: created.append(policy) or mcp, "dual_channel", "stdio", argv=[])

    assert created == ["dual_channel"]
    assert mcp.calls == [{"transport": "stdio"}]


def test_result_mode_and_http_transport_are_independent() -> None:
    mcp = RecordingMCP()
    created: list[ResultChannelPolicy] = []
    configured: list[bool] = []

    run_entrypoint(
        lambda policy: created.append(policy) or mcp,
        "content_only",
        "streamable-http",
        configure_workspace=lambda: configured.append(True),
        argv=["--result-mode", "structured", "--host", "127.0.0.1", "--port", "9137", "--path", "/tools"],
    )

    assert created == ["dual_channel"]
    assert configured == [True]
    assert mcp.calls == [{"transport": "streamable-http", "host": "127.0.0.1", "port": 9137, "path": "/tools"}]


def test_content_mode_can_use_stdio_subprocess_transport() -> None:
    mcp = RecordingMCP()
    created: list[ResultChannelPolicy] = []

    run_entrypoint(
        lambda policy: created.append(policy) or mcp,
        "dual_channel",
        "stdio",
        argv=["--result-mode", "content", "--transport", "stdio"],
    )

    assert created == ["content_only"]
    assert mcp.calls == [{"transport": "stdio"}]


def test_standard_main_forwards_its_defaults_and_user_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def run_entrypoint(*args: object, **kwargs: object) -> None:
        captured["args"] = args
        captured.update(kwargs)

    monkeypatch.setattr(standard_server, "run_entrypoint", run_entrypoint)

    standard_server.main(["--transport", "streamable-http", "--result-mode", "content", "--port", "9100"])

    assert captured["args"][1:3] == ("dual_channel", "stdio")
    assert captured["argv"] == ["--transport", "streamable-http", "--result-mode", "content", "--port", "9100"]


@pytest.mark.parametrize("argv", [["--port", "0"], ["--port", "65536"], ["--path", "tools"]])
def test_invalid_http_options_are_rejected_before_starting(argv: list[str]) -> None:
    with pytest.raises(SystemExit, match="2"):
        run_entrypoint(lambda _policy: RecordingMCP(), "dual_channel", "streamable-http", argv=argv)
