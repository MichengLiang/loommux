"""Notices: spoken in a cell, kept apart from its output, delivered once (kernel-environment-design 6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from loommux.daemon import Daemon, DaemonError
from loommux.notice import NOTICE_MAX_CHARACTERS, NOTICE_MIME, NOTICES_PER_EXECUTION
from loommux.session import IPythonSession

# Inside a session without a daemon, publishing the display data is what
# loommux.notify does.
SAY = f"from IPython.display import publish_display_data\nsay = lambda text: publish_display_data({{{NOTICE_MIME!r}: text}})\n"


@pytest.fixture
def session(tmp_path: Path) -> IPythonSession:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    value = IPythonSession()
    assert value.start_workspace(workspace, "launch_cwd")["ok"] is True
    value.run_cell(SAY)
    value.take_notices()
    yield value
    value.close()


def test_a_notice_survives_omitted_output_and_stays_out_of_every_stream(session: IPythonSession) -> None:
    response = session.run_cell("print(('abcdefghij ' * 20 + '\\n') * 301, end='')\nsay('memory is low')")

    assert response["output_omitted_reason"] == "token_limit_exceeded"
    assert session.take_notices() == [{"execution": response["execution"], "text": "memory is low"}]
    for stream in ("combined", "stdout", "stderr", "result", "traceback"):
        assert session.search_output("memory is low", stream=stream)["matched_lines"] == 0


def test_a_notice_is_delivered_once(session: IPythonSession) -> None:
    spoken = session.run_cell("say('once')")

    assert session.take_notices() == [{"execution": spoken["execution"], "text": "once"}]
    assert session.take_notices() == []


def test_a_post_run_cell_hook_speaks_for_every_cell(session: IPythonSession) -> None:
    registered = session.run_cell("get_ipython().events.register('post_run_cell', lambda result: say(f'after {result.execution_count}'))")
    later = session.run_cell("1 + 1")

    assert session.take_notices() == [
        {"execution": registered["execution"], "text": f"after {registered['execution']}"},
        {"execution": later["execution"], "text": f"after {later['execution']}"},
    ]


def test_a_running_cell_keeps_its_notices_until_it_ends(session: IPythonSession) -> None:
    running = session.run_cell("%%loommux --wait 0.1\nimport time\nsay('started')\ntime.sleep(0.5)\nsay('finished')")

    assert running["status"] == "running"
    assert session.take_notices() == []
    session.wait(running["execution"], timeout_seconds=3)
    assert [notice["text"] for notice in session.take_notices()] == ["started", "finished"]


def test_notices_are_bounded_in_length_and_number(session: IPythonSession) -> None:
    session.run_cell(f"say('x' * {NOTICE_MAX_CHARACTERS + 100})\nfor n in range({NOTICES_PER_EXECUTION + 1}):\n    say(f'n{{n}}')")

    texts = [notice["text"] for notice in session.take_notices()]

    assert texts[0] == "x" * (NOTICE_MAX_CHARACTERS - 1) + "…"
    assert texts[1:-1] == [f"n{n}" for n in range(NOTICES_PER_EXECUTION - 1)]
    assert texts[-1] == "and 2 more notices that were not kept"


def test_a_follower_takes_one_execution_s_notices_with_its_final_state(session: IPythonSession) -> None:
    spoken = session.run_cell("say('for the follower')")

    followed = session.output_since(spoken["execution"], 0)

    assert followed["notices"] == ["for the follower"]
    assert session.take_notices() == []


def test_restart_forgets_notices_nobody_took(session: IPythonSession) -> None:
    session.run_cell("say('before restart')")
    session.restart()

    assert session.take_notices() == []


def test_notify_speaks_only_from_a_loommux_kernel() -> None:
    with pytest.raises(DaemonError, match="cell of a Loommux kernel"):
        Daemon().notify("nobody hears this")
