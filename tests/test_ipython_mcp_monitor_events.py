from __future__ import annotations

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
    value.mkdir()
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
    assert all("runtime_root" not in event for event in publisher.events)
    assert publisher.closed is True


async def test_reset_publishes_killed_integer_record(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(workspace)
    publisher = RecordingPublisher()
    async with Client(create_mcp(publisher)) as client:
        running = await client.call_tool("run_python", {"freeform": "# loommux: timeout_seconds=0.1\nimport time\ntime.sleep(5)"})
        await client.call_tool("reset_python", {})

    killed = [event for event in publisher.events_of("execution_finished") if event["status"] == "killed"]
    assert killed[0]["execution"] == running.data["execution"]


async def test_true_kernel_normalizes_all_public_text_streams_and_monitor_events(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(workspace)
    publisher = RecordingPublisher()
    completed_source = (
        "import sys\n"
        "from IPython.display import display\n"
        "sys.stdout.write('stdout-start')\n"
        "sys.stdout.write('\\x1b[31')\n"
        "sys.stdout.write('mstdout-business\\x1b[0m\\n')\n"
        "sys.stdout.flush()\n"
        "sys.stderr.write('stderr-start')\n"
        "sys.stderr.write('\\x1b]0;monitor title')\n"
        "sys.stderr.write('\\x07stderr-business\\n')\n"
        "sys.stderr.flush()\n"
        "display({'text/plain': '\\x1b[36mdisplay-business\\x1b[0m'}, raw=True)\n"
        "class Styled:\n"
        "    def __repr__(self):\n"
        "        return '\\x1b[35mresult-business\\x1b[0m'\n"
        "Styled()"
    )
    error_source = "raise RuntimeError('\\x1b[31mtraceback-business\\x1b[0m')"
    async with Client(create_mcp(publisher)) as client:
        completed = await client.call_tool("run_python", {"freeform": completed_source})
        stdout = await client.call_tool("read_python_output", {"execution": 1, "stream": "stdout"})
        stderr = await client.call_tool("read_python_output", {"execution": 1, "stream": "stderr"})
        result = await client.call_tool("read_python_output", {"execution": 1, "stream": "result"})
        combined = await client.call_tool("read_python_output", {"execution": 1, "stream": "combined"})
        search = await client.call_tool("search_python_output", {"execution": 1, "stream": "combined", "query": "stdout-business", "query_mode": "literal"})
        failed = await client.call_tool("run_python", {"freeform": error_source})
        traceback = await client.call_tool("read_python_output", {"execution": 2, "stream": "traceback"})
        failed_combined = await client.call_tool("read_python_output", {"execution": 2, "stream": "combined"})

    public_text = [completed.content[0].text, stdout.content[0].text, stderr.content[0].text, result.content[0].text, combined.content[0].text, search.content[0].text, failed.content[0].text, traceback.content[0].text, failed_combined.content[0].text]
    assert all("\x1b" not in text and "\x07" not in text for text in public_text)
    assert stdout.content[0].text == "stdout-startstdout-business"
    assert stderr.content[0].text == "stderr-startstderr-business"
    assert "display-business" in result.content[0].text
    assert "result-business" in result.content[0].text
    assert "Out[1]: result-business" in combined.content[0].text
    assert "traceback-business" in traceback.content[0].text
    assert "traceback-business" in failed_combined.content[0].text
    assert "M 1 | stdout-startstdout-business" in search.content[0].text
    assert combined.content[0].text.index("stdout-business") < combined.content[0].text.index("stderr-business") < combined.content[0].text.index("display-business") < combined.content[0].text.index("result-business")
    output_events = publisher.events_of("execution_output")
    assert output_events
    assert all("\x1b" not in str(event["text"]) and "\x07" not in str(event["text"]) for event in output_events)
    assert any(event["stream"] == "result" and "display-business" in str(event["text"]) for event in output_events)
    assert any(event["stream"] == "traceback" and "traceback-business" in str(event["text"]) for event in output_events)
