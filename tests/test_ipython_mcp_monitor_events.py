from __future__ import annotations

import stat
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from loommux.mcp_ipython_server import create_mcp


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.closed = False
        self.lock = threading.Lock()

    def publish(self, event: dict[str, Any]) -> None:
        with self.lock:
            self.events.append(dict(event))

    def close(self) -> None:
        self.closed = True

    def events_of(self, type_: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event["type"] == type_]


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    value = tmp_path / "workspace"
    python = value / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f'#!/bin/sh\nexec {sys.executable} "$@"\n')
    python.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return value


async def test_execution_monitor_events_use_integer_coordinate(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(workspace)
    publisher = RecordingPublisher()
    async with Client(create_mcp(publisher)) as client:
        result = await client.call_tool("run_python", {"freeform": "print('monitor')\n42"})

    execution = result.data["execution"]
    submitted = publisher.events_of("execution_submitted")[0]
    outputs = publisher.events_of("execution_output")
    finished = publisher.events_of("execution_finished")[0]
    assert execution == 1
    assert submitted["execution"] == execution
    assert all(event["execution"] == execution for event in outputs)
    assert finished["execution"] == execution
    assert "execution_id" not in submitted
    assert "execution_id" not in finished
    assert "output_log" not in finished
    assert publisher.closed is True


async def test_reset_publishes_killed_integer_record(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(workspace)
    publisher = RecordingPublisher()
    async with Client(create_mcp(publisher)) as client:
        running = await client.call_tool("run_python", {"freeform": "# loommux: timeout_seconds=0.1\nimport time\ntime.sleep(5)"})
        await client.call_tool("reset_python", {})

    killed = [event for event in publisher.events_of("execution_finished") if event["status"] == "killed"]
    assert killed[0]["execution"] == running.data["execution"]
