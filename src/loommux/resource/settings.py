"""Read the server-owned defaults for kernel resource leases."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite

from loommux.resource.policy import (
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
    sweep_interval_seconds: float = 10
    orphan_grace_seconds: float = 30

    @classmethod
    def from_environ(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> ResourceServerSettings:
        values = os.environ if environ is None else environ
        try:
            mode = LeaseMode(values.get("LOOMMUX_LEASE_MODE", "activity").strip().lower())
        except ValueError as exc:
            raise ValueError("LOOMMUX_LEASE_MODE must be activity or heartbeat") from exc
        settings = cls(
            lease_mode=mode,
            private_activity_timeout_seconds=_positive_float(
                values,
                "LOOMMUX_PRIVATE_TTL_SECONDS",
                DEFAULT_PRIVATE_ACTIVITY_TIMEOUT_SECONDS,
            ),
            named_activity_timeout_seconds=_positive_float(
                values,
                "LOOMMUX_NAMED_TTL_SECONDS",
                DEFAULT_NAMED_ACTIVITY_TIMEOUT_SECONDS,
            ),
            heartbeat_interval_seconds=_positive_float(
                values,
                "LOOMMUX_HEARTBEAT_INTERVAL_SECONDS",
                DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
            ),
            heartbeat_timeout_seconds=_positive_float(
                values,
                "LOOMMUX_HEARTBEAT_TIMEOUT_SECONDS",
                DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
            ),
            sweep_interval_seconds=_positive_float(
                values,
                "LOOMMUX_SWEEP_INTERVAL_SECONDS",
                10,
            ),
            orphan_grace_seconds=_positive_float(
                values,
                "LOOMMUX_ORPHAN_GRACE_SECONDS",
                30,
            ),
        )
        if settings.heartbeat_timeout_seconds <= settings.heartbeat_interval_seconds:
            raise ValueError(
                "LOOMMUX_HEARTBEAT_TIMEOUT_SECONDS must be greater than "
                "LOOMMUX_HEARTBEAT_INTERVAL_SECONDS"
            )
        return settings


def _positive_float(
    environ: Mapping[str, str],
    name: str,
    default: float,
) -> float:
    value = float(environ.get(name, default))
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return value
