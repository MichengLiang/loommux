from __future__ import annotations

from pathlib import Path

import pytest

from loommux.config import load_config
from loommux.workspace_resolver import WorkspaceConfigError, resolve_workspace_launch

CODEX_EXAMPLE = Path(__file__).parents[1] / "examples" / "config" / "codex"


def test_without_a_resolver_the_workspace_is_the_launch_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launch_cwd = tmp_path / "project" / "src"
    launch_cwd.mkdir(parents=True)
    monkeypatch.chdir(launch_cwd)

    resolution = resolve_workspace_launch()

    assert resolution.workspace == launch_cwd.resolve()
    assert resolution.workspace_resolution == "launch_cwd"


@pytest.mark.parametrize("returned", ["'notebooks'", "Path('notebooks')"])
def test_a_resolver_may_return_a_path_relative_to_the_launch_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, returned: str) -> None:
    launch_cwd = tmp_path / "launch"
    workspace = launch_cwd / "notebooks"
    workspace.mkdir(parents=True)
    directory = tmp_path / "config"
    directory.mkdir()
    (directory / "config.py").write_text(f"from pathlib import Path\n\ndef resolve_workspace(launch_cwd):\n    return {returned}\n", encoding="utf-8")
    monkeypatch.chdir(launch_cwd)

    resolution = resolve_workspace_launch(load_config(directory).resolve_workspace)

    assert resolution.workspace == workspace
    assert resolution.workspace_resolution == "explicit_config"


def _raises(_launch_cwd: Path) -> Path:
    raise RuntimeError("private resolver detail")


@pytest.mark.parametrize(
    ("resolver", "expected_status"),
    [
        (_raises, "workspace_config_load_failed"),
        (lambda _launch_cwd: 7, "workspace_config_invalid_return"),
        (lambda _launch_cwd: "missing", "workspace_not_found"),
        (lambda _launch_cwd: "ordinary-file", "workspace_not_directory"),
    ],
)
def test_resolver_failures_have_canonical_statuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resolver: object, expected_status: str) -> None:
    launch_cwd = tmp_path / "launch"
    launch_cwd.mkdir()
    (launch_cwd / "ordinary-file").write_text("not a workspace", encoding="utf-8")
    monkeypatch.chdir(launch_cwd)

    with pytest.raises(WorkspaceConfigError) as error:
        resolve_workspace_launch(resolver)  # pyright: ignore[reportArgumentType]

    assert error.value.status == expected_status
    assert "private resolver detail" not in str(error.value)


def test_the_codex_example_returns_the_parent_of_the_nearest_codex_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker_directory = tmp_path / "codex-parent" / "project" / ".codex"
    launch_cwd = marker_directory.parent / "nested" / "feature"
    launch_cwd.mkdir(parents=True)
    marker_directory.mkdir()
    monkeypatch.chdir(launch_cwd)
    resolver = load_config(CODEX_EXAMPLE).resolve_workspace

    explicit = resolve_workspace_launch(resolver)
    marker_directory.rmdir()
    fallback = resolve_workspace_launch(resolver)

    assert explicit.workspace == marker_directory.parent
    assert explicit.workspace_resolution == "explicit_config"
    assert fallback.workspace == launch_cwd
    assert fallback.workspace_resolution == "explicit_config"
