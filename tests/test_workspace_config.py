from __future__ import annotations

import sys
from pathlib import Path

import pytest

from loommux.workspace_config import resolve_workspace_launch

PROJECT_CONFIG = Path(__file__).parents[1] / "loommux_workspace.py"


def test_workspace_defaults_to_the_server_process_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    workspace, python_path = resolve_workspace_launch()

    assert workspace == tmp_path
    assert python_path == Path(sys.executable).absolute()


def test_workspace_config_found_in_parent_can_choose_a_child_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    launch_cwd = project / "src" / "feature"
    selected_workspace = project / "notebooks"
    launch_cwd.mkdir(parents=True)
    selected_workspace.mkdir()
    (project / "loommux_workspace.py").write_text(
        "from pathlib import Path\n\ndef resolve_workspace(launch_cwd: Path) -> Path:\n    return launch_cwd.parents[1] / 'notebooks'\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(launch_cwd)

    workspace, python_path = resolve_workspace_launch()

    assert workspace == selected_workspace
    assert python_path == Path(sys.executable).absolute()


def test_project_config_uses_nearest_codex_ancestor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    nested = project / "src" / "feature"
    (project / ".codex").mkdir(parents=True)
    nested.mkdir(parents=True)
    (project / "loommux_workspace.py").write_text(PROJECT_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.chdir(nested)

    workspace, _python_path = resolve_workspace_launch()

    assert workspace == project


def test_project_config_falls_back_to_the_launch_cwd_without_codex_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    nested = project / "src" / "feature"
    nested.mkdir(parents=True)
    (project / "loommux_workspace.py").write_text(PROJECT_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.chdir(nested)

    workspace, _python_path = resolve_workspace_launch()

    assert workspace == nested
