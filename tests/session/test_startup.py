"""Startup files: run in every kernel process before its first cell (kernel-environment-design 5)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client

import loommux.session
from loommux.config import LoommuxConfig
from loommux.mcp.factory import create_mcp
from loommux.notice import NOTICE_MIME
from loommux.session import IPythonSession

PROBE = "defined_at_start, ipy_ran, after_broken, get_ipython().execution_count - 1"


@pytest.fixture
def startup(tmp_path: Path) -> Path:
    directory = tmp_path / "startup"
    directory.mkdir()
    (directory / "00-define.py").write_text(
        f"from IPython.display import publish_display_data\npublish_display_data({{{NOTICE_MIME!r}: 'hello from startup'}})\nprint('startup noise')\ndefined_at_start = 1\n",
        encoding="utf-8",
    )
    (directory / "10-magic.ipy").write_text("%autocall 0\nipy_ran = True\n", encoding="utf-8")
    (directory / "20-broken.py").write_text("nope\n", encoding="utf-8")
    (directory / "30-after.py").write_text("after_broken = True\n", encoding="utf-8")
    (directory / "README.txt").write_text("not python, never run\n", encoding="utf-8")
    return directory


def started(tmp_path: Path, startup: Path) -> IPythonSession:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = IPythonSession(startup_directory=startup)
    assert session.start_workspace(workspace, "launch_cwd")["ok"] is True
    return session


EXPECTED_NOTICES = [
    {"execution": None, "text": "hello from startup"},
    {"execution": None, "text": "20-broken.py failed: NameError: name 'nope' is not defined"},
]


def test_startup_files_run_in_order_before_the_first_cell_and_take_no_number(tmp_path: Path, startup: Path) -> None:
    session = started(tmp_path, startup)
    try:
        first = session.run_cell(PROBE)

        assert first["execution"] == 1
        # A failing file does not stop the files after it; IPython counts only the agent's cell.
        assert first["result_text"] == "(1, True, True, 1)"
        assert "startup noise" not in first["output_text"]
        assert session.take_notices() == EXPECTED_NOTICES
    finally:
        session.close()


def test_startup_files_run_again_after_restart(tmp_path: Path, startup: Path) -> None:
    session = started(tmp_path, startup)
    try:
        session.take_notices()
        session.run_cell("defined_at_start = 'changed'")
        session.restart()
        after = session.run_cell(PROBE)

        assert after["execution"] == 1
        assert after["result_text"] == "(1, True, True, 1)"
        assert session.take_notices() == EXPECTED_NOTICES
    finally:
        session.close()


def test_a_startup_file_that_does_not_finish_is_interrupted_and_the_next_still_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loommux.session, "STARTUP_FILE_SECONDS", 0.5)
    directory = tmp_path / "startup"
    directory.mkdir()
    (directory / "00-hangs.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    (directory / "10-next.py").write_text("ran_next = True\n", encoding="utf-8")
    session = started(tmp_path, directory)
    try:
        assert session.run_cell("ran_next")["result_text"] == "True"
        assert session.take_notices() == [{"execution": None, "text": "00-hangs.py failed: it did not finish within 0.5 s and was interrupted"}]
    finally:
        session.close()


async def test_the_configured_startup_directory_reaches_every_kernel_and_its_failures_reach_the_agent(tmp_path: Path, startup: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    async with Client(create_mcp("content", config=LoommuxConfig(startup_directory=startup))) as client:
        first = await client.call_tool("run_cell", {"freeform": "defined_at_start"})
        second = await client.call_tool("run_cell", {"freeform": "defined_at_start"})

    assert first.content[0].text == "In [1]:\nOut[1]: 1\n"
    assert first.content[1].text == "Startup · hello from startup\nStartup · 20-broken.py failed: NameError: name 'nope' is not defined"
    assert [block.text for block in second.content] == ["In [2]:\nOut[2]: 1\n"]
