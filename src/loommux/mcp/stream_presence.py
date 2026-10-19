"""Report standalone Streamable HTTP streams to the connection registry.

The observation sits at the ASGI layer and relies only on what the MCP
Streamable HTTP specification defines: a client opens its server-push stream
with ``GET`` on the MCP endpoint, carrying its ``Mcp-Session-Id``, and the
server answers ``200`` with ``text/event-stream`` for as long as the stream
lives. Unlike the ping renewal hook, nothing here depends on SDK internals.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from loommux.resource import ConnectionRegistry

SESSION_HEADER = b"mcp-session-id"


class StandaloneStreamPresence:
    """Count a session's stream as open from its 200 response until it ends.

    A rejected ``GET`` (unknown session, a second concurrent stream) never
    reaches ``200``, so it is not counted. Every other request passes through
    untouched.
    """

    def __init__(self, app: ASGIApp, connections: ConnectionRegistry) -> None:
        self.app = app
        self.connections = connections

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        session_id = _stream_session(scope)
        if session_id is None:
            await self.app(scope, receive, send)
            return

        opened = False

        async def observed_send(message: Message) -> None:
            nonlocal opened
            if not opened and _opens_event_stream(message):
                opened = True
                self.connections.stream_opened(session_id)
            await send(message)

        try:
            await self.app(scope, receive, observed_send)
        finally:
            if opened:
                self.connections.stream_closed(session_id)


def _stream_session(scope: Scope) -> str | None:
    if scope["type"] != "http" or scope["method"] != "GET":
        return None
    for name, value in scope["headers"]:
        if name == SESSION_HEADER:
            return value.decode("latin-1") or None
    return None


def _opens_event_stream(message: Message) -> bool:
    if message["type"] != "http.response.start" or message["status"] != 200:
        return False
    return any(
        name == b"content-type" and value.split(b";")[0].strip() == b"text/event-stream"
        for name, value in message.get("headers", [])
    )
