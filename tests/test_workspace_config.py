from __future__ import annotations

from pathlib import Path

import pytest

from loommux.host_workspace_config import WORKSPACE_CONFIG_ENV, WorkspaceConfigError
from loommux.workspace_resolver import resolve_workspace_launch

EXAMPLES = Path(__file__).parents[1] / "examples" / "workspace-resolvers"


def test_default_workspace_does_not_execute_legacy_files_in_the_launch_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    launch_cwd = project / "src" / "feature"
    launch_cwd.mkdir(parents=True)
    marker = tmp_path / "legacy-was-executed"
    for legacy_path in (project / "loommux_workspace.py", launch_cwd / "loommux_workspace.py"):
        legacy_path.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n", encoding="utf-8")
    monkeypatch.chdir(launch_cwd)

    resolution = resolve_workspace_launch()

    assert resolution.workspace == launch_cwd.resolve()
    assert resolution.workspace_resolution == "launch_cwd"
    assert not marker.exists()


def test_explicit_resolver_selects_relative_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launch_cwd = tmp_path / "launch"
    workspace = launch_cwd / "notebooks"
    resolver = tmp_path / "resolver.py"
    workspace.mkdir(parents=True)
    resolver.write_text("def resolve_workspace(launch_cwd):\n    return 'notebooks'\n", encoding="utf-8")
    monkeypatch.chdir(launch_cwd)
    monkeypatch.setenv(WORKSPACE_CONFIG_ENV, str(resolver))

    resolution = resolve_workspace_launch()

    assert resolution.workspace == workspace
    assert resolution.workspace_resolution == "explicit_config"


def test_explicit_resolver_accepts_a_relative_path_return(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launch_cwd = tmp_path / "launch"
    workspace = launch_cwd / "notebooks"
    resolver = tmp_path / "resolver.py"
    workspace.mkdir(parents=True)
    resolver.write_text("from pathlib import Path\n\ndef resolve_workspace(launch_cwd):\n    return Path('notebooks')\n", encoding="utf-8")
    monkeypatch.chdir(launch_cwd)
    monkeypatch.setenv(WORKSPACE_CONFIG_ENV, str(resolver))

    resolution = resolve_workspace_launch()

    assert resolution.workspace == workspace
    assert resolution.workspace_resolution == "explicit_config"


@pytest.mark.parametrize(
    "resolver_source",
    [
        "PRIVATE_RESOLVER_CONTENT = 'private resolver source'\n",
        "resolve_workspace = 'private resolver source'\n",
    ],
)
def test_config_without_callable_resolve_workspace_fails_to_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resolver_source: str) -> None:
    launch_cwd = tmp_path / "launch"
    resolver = tmp_path / "resolver.py"
    launch_cwd.mkdir()
    resolver.write_text(resolver_source, encoding="utf-8")
    monkeypatch.chdir(launch_cwd)
    monkeypatch.setenv(WORKSPACE_CONFIG_ENV, str(resolver))

    with pytest.raises(WorkspaceConfigError) as error:
        resolve_workspace_launch()

    assert error.value.status == "workspace_config_load_failed"
    assert "private resolver source" not in str(error.value)


@pytest.mark.parametrize(
    ("configured_path", "resolver_source", "expected_status"),
    [
        ("relative-resolver.py", None, "workspace_config_not_absolute"),
        ("{tmp}/missing.py", None, "workspace_config_not_found"),
        ("{tmp}/resolver-directory", None, "workspace_config_not_file"),
        ("{tmp}/load-error.py", "raise RuntimeError('private resolver detail')\n", "workspace_config_load_failed"),
        ("{tmp}/invalid-return.py", "def resolve_workspace(launch_cwd):\n    return 7\n", "workspace_config_invalid_return"),
        ("{tmp}/missing-workspace.py", "def resolve_workspace(launch_cwd):\n    return 'missing'\n", "workspace_not_found"),
        ("{tmp}/file-workspace.py", "def resolve_workspace(launch_cwd):\n    return 'ordinary-file'\n", "workspace_not_directory"),
    ],
)
def test_configured_resolver_failures_have_canonical_statuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_path: str, resolver_source: str | None, expected_status: str) -> None:
    launch_cwd = tmp_path / "launch"
    launch_cwd.mkdir()
    (tmp_path / "resolver-directory").mkdir()
    (launch_cwd / "ordinary-file").write_text("not a workspace", encoding="utf-8")
    if resolver_source is not None:
        resolver_path = Path(configured_path.format(tmp=tmp_path))
        resolver_path.write_text(resolver_source, encoding="utf-8")
    monkeypatch.chdir(launch_cwd)
    monkeypatch.setenv(WORKSPACE_CONFIG_ENV, configured_path.format(tmp=tmp_path))

    with pytest.raises(WorkspaceConfigError) as error:
        resolve_workspace_launch()

    assert error.value.status == expected_status
    assert "private resolver detail" not in str(error.value)


def test_codex_example_is_only_used_when_explicitly_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    codex_parent = tmp_path / "codex-parent"
    marker_directory = codex_parent / "project" / ".codex"
    launch_cwd = marker_directory.parent / "nested" / "feature"
    launch_cwd.mkdir(parents=True)
    marker_directory.mkdir()
    monkeypatch.chdir(launch_cwd)
    monkeypatch.setenv(WORKSPACE_CONFIG_ENV, str(EXAMPLES / "codex.py"))

    explicit = resolve_workspace_launch()

    marker_directory.rmdir()
    fallback = resolve_workspace_launch()

    assert explicit.workspace == marker_directory.parent
    assert explicit.workspace_resolution == "explicit_config"
    assert fallback.workspace == launch_cwd
    assert fallback.workspace_resolution == "explicit_config"
