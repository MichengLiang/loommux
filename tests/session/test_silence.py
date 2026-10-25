"""A wait that also returns when the cell goes quiet (silence-design), against a real kernel."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from loommux.cell_header import DEFAULT_SILENCE_SECONDS, DEFAULT_WAIT_SECONDS
from loommux.notice import NOTICE_MIME
from loommux.session import IPythonSession


@pytest.fixture
def session(tmp_path: Path) -> IPythonSession:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    value = IPythonSession()
    assert value.start_workspace(workspace, "launch_cwd")["ok"] is True
    yield value
    value.close()


def timed(call):
    started = time.monotonic()
    result = call()
    return result, time.monotonic() - started


def stop(session: IPythonSession, execution: int) -> None:
    session.interrupt()
    assert session.wait(execution, 10)["status"] in {"interrupted", "error"}


def test_a_cell_that_stops_printing_returns_after_the_silence_still_running_with_its_last_lines(session: IPythonSession) -> None:
    # S1
    result, elapsed = timed(lambda: session.run_cell("import time\nprint('built')\nprint('waiting for port 5173')\ntime.sleep(30)", wait_seconds=20, silence_seconds=1))
    try:
        assert result["status"] == "running"
        assert 1 <= result["silent_seconds"] < 3 and elapsed < 5
        assert result["recent_lines"] == ["built", "waiting for port 5173"]
    finally:
        stop(session, result["execution"])


def test_a_cell_that_keeps_printing_is_not_returned_early_and_one_that_ends_returns_at_once(session: IPythonSession) -> None:
    # S2
    steady, _ = timed(lambda: session.run_cell("import time\nfor i in range(8):\n    print(i)\n    time.sleep(0.3)", wait_seconds=20, silence_seconds=1))
    assert steady["status"] == "completed" and "silent_seconds" not in steady

    quick, elapsed = timed(lambda: session.run_cell("1 + 1", wait_seconds=20, silence_seconds=5))
    assert quick["status"] == "completed" and elapsed < 3


def test_wait_counts_silence_from_its_own_start_on_a_quiet_stretch(session: IPythonSession) -> None:
    # S3: the cell has been quiet for a second already; a new wait still gives it a full second.
    first = session.run_cell("import time\nprint('started')\ntime.sleep(30)", wait_seconds=20, silence_seconds=1)
    try:
        again, elapsed = timed(lambda: session.wait(first["execution"], 20, silence_seconds=1))
        assert again["status"] == "running" and elapsed >= 0.9
    finally:
        stop(session, first["execution"])


def test_when_both_timers_expire_together_the_result_says_silent(session: IPythonSession) -> None:
    # The cell has printed its one line and gone quiet before this wait starts.
    first = session.run_cell("import time\nprint('started')\ntime.sleep(30)", wait_seconds=20, silence_seconds=1)
    try:
        again = session.wait(first["execution"], 1, silence_seconds=1)
        assert again["status"] == "running" and again["silent_seconds"] >= 1
    finally:
        stop(session, first["execution"])


def test_a_running_result_shows_the_time_since_submission_not_since_the_call(session: IPythonSession) -> None:
    # S11: the number to hold against the estimate is the whole run so far.
    first = session.run_cell("import time\nprint('started')\ntime.sleep(30)", wait_seconds=20, silence_seconds=1)
    try:
        again = session.wait(first["execution"], 1, silence_seconds=1)
        assert first["elapsed_seconds"] >= 1 and again["elapsed_seconds"] >= first["elapsed_seconds"] + 1
    finally:
        stop(session, first["execution"])


def test_without_a_written_silence_both_waits_take_the_default(session: IPythonSession, monkeypatch: pytest.MonkeyPatch) -> None:
    # S7: what a call writes reaches the wait and what it leaves out is the default. The clock
    # itself is S1 to S3's, proved with one-second silences, so no test sits out a default.
    seen: list[tuple[float, float]] = []
    monkeypatch.setattr("loommux.session._await", lambda _record, timeout, silence, *, since: seen.append((timeout, silence)))
    execution = session.run_cell("%%loommux --wait 30\npass")["execution"]
    session.wait(execution)
    assert seen == [(30.0, DEFAULT_SILENCE_SECONDS), (DEFAULT_WAIT_SECONDS, DEFAULT_SILENCE_SECONDS)]


def test_a_redrawn_line_resets_the_clock_and_a_notice_does_not(session: IPythonSession) -> None:
    # S4
    redrawn = session.run_cell("import sys, time\nfor i in range(8):\n    sys.stdout.write(f'\\r{i * 10}%')\n    sys.stdout.flush()\n    time.sleep(0.3)", wait_seconds=20, silence_seconds=1)
    assert redrawn["status"] == "completed"

    noticed = session.run_cell(
        f"import time\nfrom IPython.display import publish_display_data\nfor i in range(20):\n    publish_display_data({{{NOTICE_MIME!r}: 'still here'}})\n    time.sleep(0.3)",
        wait_seconds=20,
        silence_seconds=1,
    )
    try:
        assert noticed["status"] == "running" and noticed["silent_seconds"] >= 1
    finally:
        stop(session, noticed["execution"])


def test_a_running_result_shows_its_last_lines_when_the_wait_runs_out_too(session: IPythonSession) -> None:
    lines = "".join(f"print('line {n}')\n" for n in range(8))
    result = session.run_cell(f"import time\n{lines}print('x' * 500)\ntime.sleep(30)", wait_seconds=1)
    try:
        assert "silent_seconds" not in result
        assert result["recent_lines"][:4] == ["line 4", "line 5", "line 6", "line 7"]
        assert len(result["recent_lines"][4]) == 200 and result["recent_lines"][4].endswith("…")
    finally:
        stop(session, result["execution"])


def test_a_python_child_under_bash_streams_its_lines_as_they_are_printed(session: IPythonSession) -> None:
    # S6: on plain pipes a Python child would buffer until it exits and look silent throughout.
    loop = "import time\\nfor i in range(6):\\n    print(i)\\n    time.sleep(0.5)"
    result = session.run_cell(f"%%bash\n{sys.executable} -c $'{loop}'", wait_seconds=20, silence_seconds=1.5)
    assert result["status"] == "completed" and "silent_seconds" not in result
    assert session.read_output(result["execution"])["text"].split() == ["0", "1", "2", "3", "4", "5"]


@pytest.mark.parametrize("silence", [0, -1, float("nan"), "soon"])
def test_silence_must_be_a_positive_number_of_seconds(session: IPythonSession, silence: object) -> None:
    assert session.run_cell("1", silence_seconds=silence)["status"] == "invalid_silence"  # type: ignore[arg-type]
    assert session.wait(None, 1, silence_seconds=silence)["status"] == "invalid_silence"  # type: ignore[arg-type]


def test_a_program_waiting_zero_seconds_submits_and_returns_at_once(session: IPythonSession) -> None:
    # Kernel.run_cell(code, wait_seconds=0) is documented as submit-and-return.
    # Short sleeps, as in test_an_interrupt_right_after_submission_interrupts_that_cell:
    # a signal that lands as a blocking call begins waits for the call to return.
    result, elapsed = timed(lambda: session.run_cell("import time\nfor _ in range(30):\n    time.sleep(0.1)", wait_seconds=0))
    assert result["status"] == "running" and elapsed < 1
    session.interrupt()
    assert session.wait(result["execution"], 10)["status"] == "interrupted"
    assert session.run_cell("1", wait_seconds=-1)["status"] == "invalid_timeout"
    assert session.wait(None, 0)["status"] == "invalid_timeout"
