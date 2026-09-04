"""Read the server-owned defaults for kernel resource leases."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from loommux.resource.policy import LeaseMode


@dataclass(frozen=True)
class ResourceServerSettings:
    """Validated process-level defaults used to build lease policy generation one."""

    lease_mode: LeaseMode = LeaseMode.ACTIVITY
    private_activity_timeout_seconds: float = 30 * 60
    named_activity_timeout_seconds: float = 24 * 60 * 60
    heartbeat_interval_seconds: float = 15
    heartbeat_timeout_seconds: float = 60
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
        return cls(
            lease_mode=mode,
            private_activity_timeout_seconds=_positive_float(
                values,
                "LOOMMUX_PRIVATE_TTL_SECONDS",
                30 * 60,
            ),
            named_activity_timeout_seconds=_positive_float(
                values,
                "LOOMMUX_NAMED_TTL_SECONDS",
                24 * 60 * 60,
            ),
            heartbeat_interval_seconds=_positive_float(
                values,
                "LOOMMUX_HEARTBEAT_INTERVAL_SECONDS",
                15,
            ),
            heartbeat_timeout_seconds=_positive_float(
                values,
                "LOOMMUX_HEARTBEAT_TIMEOUT_SECONDS",
                60,
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


def _positive_float(
    environ: Mapping[str, str],
    name: str,
    default: float,
) -> float:
    value = float(environ.get(name, default))
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return value
