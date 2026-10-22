"""Maintain immutable, generation-addressable client lease policies."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite


class LeaseMode(StrEnum):
    """How a client proves that it still participates in a resource."""

    ACTIVITY = "activity"
    HEARTBEAT = "heartbeat"
    CONNECTION = "connection"


# Generation-one lease durations are declared exactly once here. Server settings
# read environment overrides against these fallbacks, and a policy manager built
# without explicit values adopts the same contract. Two declaration sites let the
# advertised default and the effective default drift apart: the 80-minute
# private timeout existed only on the settings dataclass while the environment
# reader still fell back to the earlier 30 minutes.
DEFAULT_PRIVATE_ACTIVITY_TIMEOUT_SECONDS: float = 80 * 60
DEFAULT_NAMED_ACTIVITY_TIMEOUT_SECONDS: float = 24 * 60 * 60
DEFAULT_HEARTBEAT_INTERVAL_SECONDS: float = 15
DEFAULT_HEARTBEAT_TIMEOUT_SECONDS: float = 60
# How long a connection lease waits for a standalone stream, either the first
# one or a replacement after a drop, before deciding. The MCP TypeScript SDK
# that Claude Code embeds retries a dropped stream twice, 1 s and then 1.5 s
# apart, so a minute absorbs any reconnect with wide margin. It decides alerts
# and when a departed session's lease is released; the main kernel's own
# retention, not this grace, decides how long the kernel waits.
DEFAULT_CONNECTION_GRACE_SECONDS: float = 60


@dataclass(frozen=True)
class LeasePolicy:
    """One immutable lease contract retained by already-created leases."""

    mode: LeaseMode
    generation: int
    private_activity_timeout_seconds: float
    named_activity_timeout_seconds: float
    heartbeat_interval_seconds: float
    heartbeat_timeout_seconds: float
    connection_grace_seconds: float

    def timeout_for(self, *, named: bool) -> float:
        """Return the renewal window that expires an unrenewed lease.

        ``named`` selects the named window for a main kernel a session chose
        or renamed, and the private window for one whose name was generated.
        A connection lease is held by its open stream instead; it uses the
        activity window only after it has degraded to activity semantics.
        """
        if self.mode is LeaseMode.HEARTBEAT:
            return self.heartbeat_timeout_seconds
        if named:
            return self.named_activity_timeout_seconds
        return self.private_activity_timeout_seconds

    def as_dict(self) -> dict[str, str | int | float]:
        return {
            "mode": self.mode.value,
            "generation": self.generation,
            "private_activity_timeout_seconds": self.private_activity_timeout_seconds,
            "named_activity_timeout_seconds": self.named_activity_timeout_seconds,
            "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
            "heartbeat_timeout_seconds": self.heartbeat_timeout_seconds,
            "connection_grace_seconds": self.connection_grace_seconds,
        }


class LeasePolicyManager:
    """Publish current defaults while preserving every referenced generation."""

    def __init__(
        self,
        *,
        initial_mode: LeaseMode = LeaseMode.ACTIVITY,
        private_activity_timeout_seconds: float = DEFAULT_PRIVATE_ACTIVITY_TIMEOUT_SECONDS,
        named_activity_timeout_seconds: float = DEFAULT_NAMED_ACTIVITY_TIMEOUT_SECONDS,
        heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        heartbeat_timeout_seconds: float = DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
        connection_grace_seconds: float = DEFAULT_CONNECTION_GRACE_SECONDS,
    ) -> None:
        self._validate(
            private_activity_timeout_seconds=private_activity_timeout_seconds,
            named_activity_timeout_seconds=named_activity_timeout_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
            connection_grace_seconds=connection_grace_seconds,
        )
        self._policy = LeasePolicy(
            mode=initial_mode,
            generation=1,
            private_activity_timeout_seconds=private_activity_timeout_seconds,
            named_activity_timeout_seconds=named_activity_timeout_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
            connection_grace_seconds=connection_grace_seconds,
        )
        self._by_generation = {self._policy.generation: self._policy}
        self._lock = asyncio.Lock()

    async def current(self) -> LeasePolicy:
        async with self._lock:
            return self._policy

    async def generation(self, generation: int) -> LeasePolicy | None:
        async with self._lock:
            return self._by_generation.get(generation)

    async def update(
        self,
        *,
        mode: LeaseMode,
        private_activity_timeout_seconds: float,
        named_activity_timeout_seconds: float,
        heartbeat_interval_seconds: float,
        heartbeat_timeout_seconds: float,
        connection_grace_seconds: float,
    ) -> LeasePolicy:
        self._validate(
            private_activity_timeout_seconds=private_activity_timeout_seconds,
            named_activity_timeout_seconds=named_activity_timeout_seconds,
            heartbeat_interval_seconds=heartbeat_interval_seconds,
            heartbeat_timeout_seconds=heartbeat_timeout_seconds,
            connection_grace_seconds=connection_grace_seconds,
        )
        async with self._lock:
            current = self._policy
            values = (
                mode,
                private_activity_timeout_seconds,
                named_activity_timeout_seconds,
                heartbeat_interval_seconds,
                heartbeat_timeout_seconds,
                connection_grace_seconds,
            )
            current_values = (
                current.mode,
                current.private_activity_timeout_seconds,
                current.named_activity_timeout_seconds,
                current.heartbeat_interval_seconds,
                current.heartbeat_timeout_seconds,
                current.connection_grace_seconds,
            )
            if values == current_values:
                return current

            policy = LeasePolicy(
                mode=mode,
                generation=current.generation + 1,
                private_activity_timeout_seconds=private_activity_timeout_seconds,
                named_activity_timeout_seconds=named_activity_timeout_seconds,
                heartbeat_interval_seconds=heartbeat_interval_seconds,
                heartbeat_timeout_seconds=heartbeat_timeout_seconds,
                connection_grace_seconds=connection_grace_seconds,
            )
            self._policy = policy
            self._by_generation[policy.generation] = policy
            return policy

    @staticmethod
    def _validate(
        *,
        private_activity_timeout_seconds: float,
        named_activity_timeout_seconds: float,
        heartbeat_interval_seconds: float,
        heartbeat_timeout_seconds: float,
        connection_grace_seconds: float,
    ) -> None:
        durations = {
            "private activity timeout": private_activity_timeout_seconds,
            "named activity timeout": named_activity_timeout_seconds,
            "heartbeat interval": heartbeat_interval_seconds,
            "heartbeat timeout": heartbeat_timeout_seconds,
            "connection grace": connection_grace_seconds,
        }
        for label, value in durations.items():
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{label} must be a positive finite number")
        if heartbeat_timeout_seconds <= heartbeat_interval_seconds:
            raise ValueError("heartbeat timeout must be greater than heartbeat interval")
