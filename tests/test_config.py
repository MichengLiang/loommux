from __future__ import annotations

import faulthandler
import sys
from pathlib import Path

import pytest

from loommux.config import ConfigError, LoommuxConfig, load_config
from loommux.mcp.entrypoints import run_entrypoint
from loommux.resource import LeaseMode
from loommux.resource.settings import ResourceServerSettings

EXAMPLES = Path(__file__).parents[1] / "examples"


def configuration(tmp_path: Path, source: str | None = None, *, startup: bool = False) -> Path:
    directory = tmp_path / ".loommux"
    directory.mkdir()
    if source is not None:
        (directory / "config.py").write_text(source, encoding="utf-8")
    if startup:
        (directory / "startup").mkdir()
    return directory


def test_config_py_names_are_the_settings(tmp_path: Path) -> None:
    directory = configuration(
        tmp_path,
        "lease_mode = 'connection'\nnamed_activity_timeout_seconds = 3600\nconnection_grace_seconds = 2.5\n",
        startup=True,
    )

    config = load_config(directory)

    assert config.settings == ResourceServerSettings(
        lease_mode=LeaseMode.CONNECTION,
        named_activity_timeout_seconds=3600,
        connection_grace_seconds=2.5,
    )
    assert config.resolve_workspace is None
    assert config.startup_directory == directory / "startup"


def test_a_config_file_may_import_and_define_helpers(tmp_path: Path) -> None:
    source = (
        "from __future__ import annotations\n"
        "import math\n"
        "from pathlib import Path\n"
        "_hours = 3\n"
        "def hours(n):\n"
        "    return n * 3600\n"
        "named_activity_timeout_seconds = hours(_hours)\n"
        "def resolve_workspace(launch_cwd):\n"
        "    return Path(launch_cwd)\n"
    )

    config = load_config(configuration(tmp_path, source))

    assert config.settings.named_activity_timeout_seconds == 3 * 3600
    assert config.resolve_workspace is not None
    assert config.resolve_workspace(tmp_path) == tmp_path


def test_a_directory_without_config_py_or_startup_configures_nothing(tmp_path: Path) -> None:
    assert load_config(configuration(tmp_path)) == LoommuxConfig()


def test_the_shipped_example_directories_load() -> None:
    assert load_config(EXAMPLES / "lease-aware-client" / "config").settings.lease_mode is LeaseMode.HEARTBEAT
    assert load_config(EXAMPLES / "config" / "codex").resolve_workspace is not None


def test_a_relative_directory_is_read_from_the_current_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configuration(tmp_path, "orphan_grace_seconds = 5\n")
    monkeypatch.chdir(tmp_path)

    assert load_config(".loommux").settings.orphan_grace_seconds == 5


@pytest.mark.parametrize(
    ("source", "status", "named"),
    [
        ("raise RuntimeError('broken on purpose')\n", "config_load_failed", "RuntimeError: broken on purpose"),
        ("retention_seconds = 60\n", "config_unknown_setting", "retention_seconds"),
        ("lease_mode = 'forever'\n", "config_invalid_setting", "lease_mode"),
        ("sweep_interval_seconds = 0\n", "config_invalid_setting", "sweep_interval_seconds"),
        ("orphan_grace_seconds = True\n", "config_invalid_setting", "orphan_grace_seconds"),
        ("connection_grace_seconds = '60'\n", "config_invalid_setting", "connection_grace_seconds"),
        ("heartbeat_interval_seconds = 5\nheartbeat_timeout_seconds = 5\n", "config_invalid_setting", "heartbeat_timeout_seconds"),
        ("resolve_workspace = 'here'\n", "config_invalid_setting", "resolve_workspace"),
    ],
)
def test_a_config_py_that_cannot_be_used_says_why(tmp_path: Path, source: str, status: str, named: str) -> None:
    with pytest.raises(ConfigError) as error:
        load_config(configuration(tmp_path, source))

    assert error.value.status == status
    assert named in str(error.value)


def test_a_missing_or_non_directory_path_is_refused(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("", encoding="utf-8")

    with pytest.raises(ConfigError) as missing:
        load_config(tmp_path / "absent")
    with pytest.raises(ConfigError) as not_directory:
        load_config(tmp_path / "file")

    assert missing.value.status == "config_not_found"
    assert not_directory.value.status == "config_not_directory"


class RecordingMCP:
    def run(self, **_kwargs: object) -> None:
        pass


@pytest.fixture
def _no_stack_dump_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform != "win32":
        monkeypatch.setattr(faulthandler, "register", lambda *_args, **_kwargs: None)


@pytest.mark.usefixtures("_no_stack_dump_handler")
def test_the_entrypoint_reads_only_the_directory_named_by_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = tmp_path / "config-ran"
    configuration(tmp_path, f"from pathlib import Path\nPath({str(marker)!r}).touch()\nlease_mode = 'connection'\n")
    monkeypatch.chdir(tmp_path)
    received: list[LoommuxConfig] = []

    run_entrypoint(lambda _mode, _url, config: received.append(config) or RecordingMCP(), argv=[])
    # A .loommux in the launch directory is not discovered.
    assert received == [LoommuxConfig()]
    assert not marker.exists()

    run_entrypoint(lambda _mode, _url, config: received.append(config) or RecordingMCP(), argv=["--config", ".loommux"])
    assert received[1].settings.lease_mode is LeaseMode.CONNECTION
    assert marker.exists()


@pytest.mark.usefixtures("_no_stack_dump_handler")
def test_an_unusable_config_stops_the_entrypoint_before_any_server(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    directory = configuration(tmp_path, "retention_seconds = 60\n")

    with pytest.raises(SystemExit) as stopped:
        run_entrypoint(lambda *_args: pytest.fail("no server may be built"), argv=["--config", str(directory)])

    assert stopped.value.code == 2
    assert "config_unknown_setting" in capsys.readouterr().err
