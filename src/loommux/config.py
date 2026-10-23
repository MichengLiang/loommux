"""Find and read loommux's configuration directory.

The directory holds ``config.py``, run once in the daemon's own process, and
``startup/``, whose files every kernel runs when it starts; each is optional.
It is the nearest ``.loommux`` at or above the launch directory, as git finds
``.git``, or the one ``--config DIR`` names. The rules and their reasons are in
docs/kernel-environment-design.md, section 4.
"""

from __future__ import annotations
import __future__

import importlib.util
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from loommux.resource.settings import SETTING_NAMES, ResourceServerSettings
from loommux.workspace_resolver import WorkspaceResolver

CONFIG_FILE = "config.py"
CONFIG_DIRECTORY = ".loommux"
STARTUP_DIRECTORY = "startup"
RESOLVER_NAME = "resolve_workspace"
# What `from __future__ import annotations` binds in a module; the class is private.
_FUTURE_FEATURE = type(__future__.annotations)


class ConfigError(RuntimeError):
    """A configuration directory that cannot be used; ``status`` classifies why."""

    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class LoommuxConfig:
    """What a configuration directory says; the defaults are a start without one."""

    settings: ResourceServerSettings = field(default_factory=ResourceServerSettings)
    resolve_workspace: WorkspaceResolver | None = None
    startup_directory: Path | None = None


def find_config(start: Path) -> Path | None:
    """The nearest ``.loommux`` directory at ``start`` or above it, or None."""
    start = start.resolve()
    for directory in (start, *start.parents):
        if (candidate := directory / CONFIG_DIRECTORY).is_dir():
            return candidate
    return None


def load_config(directory: str | Path) -> LoommuxConfig:
    """Run ``config.py`` of ``directory`` and validate what it set.

    A relative ``directory`` is read from the current directory, as every
    command-line path is.
    """
    path = Path(directory).expanduser().resolve()
    if not path.exists():
        raise ConfigError("config_not_found", f"configuration directory {path} does not exist")
    if not path.is_dir():
        raise ConfigError("config_not_directory", f"{path} is not a directory")
    config_file = path / CONFIG_FILE
    names = _settings_of(config_file) if config_file.is_file() else {}
    resolver = names.pop(RESOLVER_NAME, None)
    if resolver is not None and not callable(resolver):
        raise ConfigError("config_invalid_setting", f"{config_file}: {RESOLVER_NAME} must be a function of the launch directory")
    if unknown := sorted(set(names) - set(SETTING_NAMES)):
        raise ConfigError(
            "config_unknown_setting",
            f"{config_file} sets {', '.join(unknown)}, which loommux does not read; its settings are {', '.join(SETTING_NAMES)} and {RESOLVER_NAME}",
        )
    try:
        settings = ResourceServerSettings.from_values(names)
    except ValueError as exc:
        raise ConfigError("config_invalid_setting", f"{config_file}: {exc}") from None
    startup = path / STARTUP_DIRECTORY
    # What the resolver returns is checked when it is called, against the launch directory.
    return LoommuxConfig(settings, cast(WorkspaceResolver | None, resolver), startup if startup.is_dir() else None)


def _settings_of(config_file: Path) -> dict[str, object]:
    spec = importlib.util.spec_from_file_location("_loommux_config", config_file)
    if spec is None or spec.loader is None:
        raise ConfigError("config_load_failed", f"{config_file} cannot be loaded as a Python module")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ConfigError("config_load_failed", f"{config_file} raised {type(exc).__name__}: {exc}") from exc
    return {name: value for name, value in vars(module).items() if _is_read(name, value)}


def _is_read(name: str, value: object) -> bool:
    """Whether a module-level name of ``config.py`` is one loommux reads.

    A configuration file is a small program: what it imports, the helpers it
    defines, its private names and its ``from __future__`` features are its
    own. Every other name must be a setting, so a misspelled one is reported
    instead of leaving a default in force.
    """
    if name.startswith("_"):
        return False
    if name == RESOLVER_NAME:
        return True
    return not (inspect.ismodule(value) or isinstance(value, type | _FUTURE_FEATURE) or callable(value))
