from __future__ import annotations

import stat
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from loommux.adapter import IPythonMCPAdapter
from loommux.mcp_ipython_content_server import create_mcp as create_content_mcp
from loommux.mcp_ipython_server import create_mcp
from loommux.monitoring import NoopMonitorPublisher


class RecordingMonitorPublisher:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.closed = False
        self._lock = threading.Lock()

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            self.events.append(dict(event))

    def close(self) -> None:
        with self._lock:
            self.closed = True

    def by_type(self, event_type: str) -> list[dict[str, Any]]:
        with self._lock:
            return [event for event in self.events if event.get("type") == event_type]


@pytest.fixture
def valid_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    python_path = workspace / ".venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n")
    python_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return workspace


def wait_for_event_count(publisher: RecordingMonitorPublisher, event_type: str, count: int, timeout_seconds: float = 3) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        events = publisher.by_type(event_type)
        if len(events) >= count:
            return events
        time.sleep(0.01)
    return publisher.by_type(event_type)


async def test_mcp_python_status_emits_tool_started_and_finished_events() -> None:
    publisher = RecordingMonitorPublisher()
    async with Client(create_mcp(monitor_publisher=publisher)) as client:
        result = await client.call_tool("python_status", {})

    started = publisher.by_type("tool_call_started")
    finished = publisher.by_type("tool_call_finished")

    assert result.data["kernel_started"] is False
    assert len(started) == 1
    assert len(finished) == 1
    assert started[0]["tool_name"] == "python_status"
    assert started[0]["call_id"] == finished[0]["call_id"]
    assert started[0]["arguments"] == {}
    assert finished[0]["tool_name"] == "python_status"
    assert finished[0]["ok"] is True
    assert finished[0]["status"] == "ok"
    assert isinstance(finished[0]["duration_ms"], int | float)
    assert "kernel_started=False" in finished[0]["result_summary"]
    assert "kernel: not_started" in finished[0]["pretty_text_summary"]
    assert publisher.closed is True


async def test_content_only_server_records_tool_events_without_structured_response() -> None:
    publisher = RecordingMonitorPublisher()
    async with Client(create_content_mcp(monitor_publisher=publisher)) as client:
        result = await client.call_tool("python_status", {})

    finished = publisher.by_type("tool_call_finished")

    assert result.data is None
    assert len(finished) == 1
    assert finished[0]["tool_name"] == "python_status"
    assert finished[0]["ok"] is True
    assert "kernel: not_started" in finished[0]["pretty_text_summary"]


async def test_run_python_emits_execution_lifecycle_and_output_events(valid_workspace: Path) -> None:
    publisher = RecordingMonitorPublisher()
    async with Client(create_mcp(monitor_publisher=publisher)) as client:
        await client.call_tool("set_workspace", {"path": str(valid_workspace)})
        result = await client.call_tool("run_python", {"freeform": "import sys\nprint('stdout-event')\nprint('stderr-event', file=sys.stderr)\n'RESULT-EVENT'"})

    submitted = publisher.by_type("execution_submitted")
    output = wait_for_event_count(publisher, "execution_output", 3)
    finished = publisher.by_type("execution_finished")
    run_finished = [event for event in publisher.by_type("tool_call_finished") if event["tool_name"] == "run_python"]

    assert result.data["status"] == "completed"
    assert len(submitted) == 1
    assert submitted[0]["execution_id"] == result.data["execution_id"]
    assert submitted[0]["call_id"] == run_finished[0]["call_id"]
    assert submitted[0]["workspace"] == str(valid_workspace.resolve())
    assert submitted[0]["kernel_pid"] == result.data["kernel"]["kernel_pid"]
    assert submitted[0]["code"].endswith("'RESULT-EVENT'")
    assert submitted[0]["timeout_seconds"] == 10.0

    streams = {event["stream"]: event for event in output}
    assert "stdout-event" in streams["stdout"]["text"]
    assert "stderr-event" in streams["stderr"]["text"]
    assert "'RESULT-EVENT'" in streams["result"]["text"]
    assert isinstance(streams["result"]["execution_count"], int)

    assert len(finished) == 1
    assert finished[0]["execution_id"] == result.data["execution_id"]
    assert finished[0]["status"] == "completed"
    assert finished[0]["output_log"] == result.data["output_log"]
    assert finished[0]["output_total_lines"] >= 3
    assert finished[0]["error"] is None


async def test_traceback_output_stream_is_distinct(valid_workspace: Path) -> None:
    publisher = RecordingMonitorPublisher()
    async with Client(create_mcp(monitor_publisher=publisher)) as client:
        await client.call_tool("set_workspace", {"path": str(valid_workspace)})
        result = await client.call_tool("run_python", {"freeform": "1 / 0"})

    output = wait_for_event_count(publisher, "execution_output", 1)
    traceback_events = [event for event in output if event["stream"] == "traceback"]

    assert result.data["status"] == "error"
    assert traceback_events
    assert "ZeroDivisionError" in traceback_events[0]["text"]
    assert traceback_events[0]["execution_id"] == result.data["execution_id"]
    assert publisher.by_type("execution_finished")[0]["status"] == "error"
    assert publisher.by_type("execution_finished")[0]["error"]["ename"] == "ZeroDivisionError"


async def test_reset_emits_killed_execution_finished_event(valid_workspace: Path) -> None:
    publisher = RecordingMonitorPublisher()
    async with Client(create_mcp(monitor_publisher=publisher)) as client:
        await client.call_tool("set_workspace", {"path": str(valid_workspace)})
        running = await client.call_tool("run_python", {"freeform": "# loommux: timeout_seconds=0.1\nimport time\ntime.sleep(10)"})
        await client.call_tool("reset_python", {})

    killed = [event for event in publisher.by_type("execution_finished") if event["status"] == "killed"]

    assert killed
    assert killed[0]["execution_id"] == running.data["execution_id"]
    assert killed[0]["output_log"] == running.data["output_log"]


def test_noop_monitor_publisher_does_not_affect_run_python(valid_workspace: Path) -> None:
    adapter = IPythonMCPAdapter(monitor_publisher=NoopMonitorPublisher())
    try:
        assert adapter.set_workspace(str(valid_workspace))["ok"] is True
        result = adapter.run_python("40 + 2")

        assert result["status"] == "completed"
        assert result["result_text"] == "42"
    finally:
        adapter.close()
