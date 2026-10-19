"""Record which MCP sessions currently hold a server-push channel open.

Streamable HTTP lets a client keep one standalone GET stream per session for
server-initiated messages. A client that keeps that stream open for its whole
lifetime makes "the stream is open" an observable proxy for "the client is
still there": when the client process exits, its socket closes and so does
the stream. A stdio session is the server process's own standard streams, so
it is connected exactly as long as this process lives.

The registry only records these transport facts. Turning them into a lease
decision belongs to :mod:`loommux.resource.liveness`.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from time import monotonic


@dataclass
class SessionPresence:
    """Transport facts about one MCP session; timestamps are monotonic.

    ``closed_at`` is when the open-stream count last fell to zero, so it is
    ``None`` exactly when the session has never had a stream to lose.
    """

    first_seen_at: float
    open_streams: int = 0
    closed_at: float | None = None
    process_bound: bool = False

    @property
    def connected(self) -> bool:
        return self.process_bound or self.open_streams > 0


class ConnectionRegistry:
    """Per-session presence, mutated only from the server's event loop.

    Every method is synchronous and never awaits, so asyncio callers observe
    each update atomically without a lock.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, SessionPresence] = {}

    def presence(self, session_id: str, *, now: float | None = None) -> SessionPresence:
        """Return the session's presence, starting its record on first sight."""
        record = self._sessions.get(session_id)
        if record is None:
            record = SessionPresence(first_seen_at=monotonic() if now is None else now)
            self._sessions[session_id] = record
        return record

    def stream_opened(self, session_id: str, *, now: float | None = None) -> None:
        record = self.presence(session_id, now=now)
        record.open_streams += 1

    def stream_closed(self, session_id: str, *, now: float | None = None) -> None:
        record = self.presence(session_id, now=now)
        record.open_streams = max(0, record.open_streams - 1)
        if record.open_streams == 0:
            record.closed_at = monotonic() if now is None else now

    def bind_to_process(self, session_id: str) -> None:
        self.presence(session_id).process_bound = True

    def prune(self, *, keep: Collection[str]) -> None:
        """Forget disconnected sessions that no lease refers to.

        A forgotten session that later calls a tool starts a fresh record and
        is judged as never having opened a stream, which still raises the
        unbound alert if no stream follows; nothing needs a retention window.
        """
        for session_id in [
            session_id
            for session_id, record in self._sessions.items()
            if not record.connected and session_id not in keep
        ]:
            del self._sessions[session_id]

    def __contains__(self, session_id: object) -> bool:
        return session_id in self._sessions
