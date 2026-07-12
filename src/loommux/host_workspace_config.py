"""Load a workspace resolver explicitly authorized by the MCP host."""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from types import ModuleType
from typing import cast

WORKSPACE_CONFIG_ENV = "LOOMMUX_WORKSPACE_CONFIG"
WorkspaceResolver = Callable[[Path], str | Path]


class WorkspaceConfigError(RuntimeError):
    """A host workspace configuration failure with its public classification."""

    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status


def load_workspace_resolver(environ: Mapping[str, str] | None = None) -> WorkspaceResolver | None:
    """Return the explicitly configured resolver, or ``None`` for the default."""
    environment = os.environ if environ is None else environ
    configured_path = environment.get(WORKSPACE_CONFIG_ENV)
    if configured_path is None:
        return None

    config_path = Path(configured_path)
    if not config_path.is_absolute():
        raise WorkspaceConfigError("workspace_config_not_absolute", f"{WORKSPACE_CONFIG_ENV} must be an absolute path")
    if not config_path.exists():
        raise WorkspaceConfigError("workspace_config_not_found", "workspace configuration file does not exist")
    if not config_path.is_file():
        raise WorkspaceConfigError("workspace_config_not_file", "workspace configuration path is not a file")

    module = _load_module(config_path)
    resolver = getattr(module, "resolve_workspace", None)
    if not callable(resolver):
        raise WorkspaceConfigError("workspace_config_load_failed", "workspace configuration must define callable resolve_workspace")
    return cast(WorkspaceResolver, resolver)


def execute_workspace_resolver(resolver: WorkspaceResolver, launch_cwd: Path) -> str | Path:
    """Execute the host-authorized resolver without exposing its exception details."""
    try:
        return resolver(launch_cwd)
    except Exception as exc:
        raise WorkspaceConfigError("workspace_config_load_failed", "workspace resolver failed while executing") from exc


def _load_module(config_path: Path) -> ModuleType:
    try:
        spec = importlib.util.spec_from_file_location("_loommux_host_workspace_config", config_path)
        if spec is None or spec.loader is None:
            raise ImportError("unable to create module loader")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:
        raise WorkspaceConfigError("workspace_config_load_failed", "workspace configuration could not be loaded") from exc
    return module
