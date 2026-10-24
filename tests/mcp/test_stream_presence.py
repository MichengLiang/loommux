import asyncio

from starlette.middleware import Middleware

from loommux.mcp.server import create_mcp
from loommux.mcp.stream_presence import StandaloneStreamPresence
from loommux.resource import ConnectionRegistry


def http_scope(method: str, headers: list[tuple[bytes, bytes]], client: list | None = None) -> dict:
    return {"type": "http", "method": method, "path": "/mcp", "headers": headers, "client": client}


def responding_app(status: int, content_type: bytes, observed: list[bool], registry: ConnectionRegistry):
    async def app(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", content_type)]})
        observed.append("session" in registry and registry.presence("session").connected)
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    return app


async def call(app, scope) -> None:
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message) -> None:
        return None

    await app(scope, receive, send)


def test_an_accepted_standalone_stream_is_open_until_its_response_ends() -> None:
    registry = ConnectionRegistry()
    observed: list[bool] = []
    middleware = StandaloneStreamPresence(
        responding_app(200, b"text/event-stream; charset=utf-8", observed, registry),
        registry,
    )

    asyncio.run(call(middleware, http_scope("GET", [(b"mcp-session-id", b"session")])))

    assert observed == [True]
    presence = registry.presence("session")
    assert not presence.connected
    assert presence.closed_at is not None


def test_an_open_stream_carries_the_address_it_came_from_until_it_ends() -> None:
    registry = ConnectionRegistry()
    seen: list[object] = []

    async def app(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/event-stream")]})
        seen.append(registry.presence("session").client_address)
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    scope = http_scope("GET", [(b"mcp-session-id", b"session")], client=["127.0.0.1", 45900])
    asyncio.run(call(StandaloneStreamPresence(app, registry), scope))

    assert seen == [("127.0.0.1", 45900)]
    assert registry.presence("session").client_address is None


def test_rejected_streams_and_other_requests_are_not_counted() -> None:
    registry = ConnectionRegistry()
    observed: list[bool] = []
    rejected = StandaloneStreamPresence(responding_app(409, b"application/json", observed, registry), registry)
    json_reply = StandaloneStreamPresence(responding_app(200, b"application/json", observed, registry), registry)

    asyncio.run(call(rejected, http_scope("GET", [(b"mcp-session-id", b"session")])))
    asyncio.run(call(json_reply, http_scope("GET", [(b"mcp-session-id", b"session")])))
    asyncio.run(call(json_reply, http_scope("POST", [(b"mcp-session-id", b"session")])))
    asyncio.run(call(json_reply, http_scope("GET", [])))

    assert observed == [False, False, False, False]
    assert "session" not in registry


def test_non_http_scopes_pass_through_untouched() -> None:
    registry = ConnectionRegistry()
    seen: list[str] = []

    async def lifespan_app(scope, receive, send) -> None:
        seen.append(scope["type"])

    asyncio.run(call(StandaloneStreamPresence(lifespan_app, registry), {"type": "lifespan"}))

    assert seen == ["lifespan"]


def test_the_http_application_observes_standalone_streams() -> None:
    app = create_mcp().http_app(path="/mcp")

    assert any(
        isinstance(entry, Middleware) and entry.cls is StandaloneStreamPresence
        for entry in app.user_middleware
    )
