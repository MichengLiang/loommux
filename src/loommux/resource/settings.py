"""Validate the lease settings a configuration directory's ``config.py`` names."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from math import isfinite

from loommux.resource.policy import (
    DEFAULT_CONNECTION_GRACE_SECONDS,
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
    DEFAULT_NAMED_ACTIVITY_TIMEOUT_SECONDS,
    DEFAULT_PRIVATE_ACTIVITY_TIMEOUT_SECONDS,
    LeaseMode,
)


@dataclass(frozen=True)
class ResourceServerSettings:
    """Validated process-level defaults used to build lease policy generation one."""

    lease_mode: LeaseMode = LeaseMode.ACTIVITY
    private_activity_timeout_seconds: float = DEFAULT_PRIVATE_ACTIVITY_TIMEOUT_SECONDS
    named_activity_timeout_seconds: float = DEFAULT_NAMED_ACTIVITY_TIMEOUT_SECONDS
    heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS
    heartbeat_timeout_seconds: float = DEFAULT_HEARTBEAT_TIMEOUT_SECONDS
    connection_grace_seconds: float = DEFAULT_CONNECTION_GRACE_SECONDS
    sweep_interval_seconds: float = 10
    orphan_grace_seconds: float = 30

    @classmethod
    def from_values(cls, values: Mapping[str, object]) -> ResourceServerSettings:
        """Settings from names a ``config.py`` set; an absent name keeps its default.

        Raises ``ValueError`` naming the setting whose value is wrong.
        """
        defaults = cls()
        mode = values.get("lease_mode", defaults.lease_mode.value)
        try:
            lease_mode = LeaseMode(mode.strip().lower()) if isinstance(mode, str) else None
        except ValueError:
            lease_mode = None
        if lease_mode is None:
            raise ValueError(f"lease_mode must be 'activity', 'heartbeat', or 'connection', not {mode!r}")
        durations = {name: _positive_seconds(name, values.get(name, getattr(defaults, name))) for name in SETTING_NAMES if name != "lease_mode"}
        settings = cls(lease_mode=lease_mode, **durations)
        if settings.heartbeat_timeout_seconds <= settings.heartbeat_interval_seconds:
            raise ValueError("heartbeat_timeout_seconds must be greater than heartbeat_interval_seconds")
        return settings


SETTING_NAMES = tuple(setting.name for setting in fields(ResourceServerSettings))


def _positive_seconds(name: str, value: object) -> float:
    # bool is an int to Python, but `orphan_grace_seconds = True` is a mistake.
    if isinstance(value, bool) or not isinstance(value, int | float) or not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number of seconds, not {value!r}")
    return float(value)
