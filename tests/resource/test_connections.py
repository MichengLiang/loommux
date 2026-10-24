from loommux.resource.connections import ConnectionRegistry


def test_presence_counts_overlapping_streams_and_marks_only_the_last_close() -> None:
    registry = ConnectionRegistry()

    registry.stream_opened("session", now=1)
    registry.stream_opened("session", now=2)
    registry.stream_closed("session", now=3)
    presence = registry.presence("session")

    assert presence.first_seen_at == 1
    assert presence.connected
    assert presence.closed_at is None

    registry.stream_closed("session", now=4)

    assert not presence.connected
    assert presence.closed_at == 4


def test_presence_names_the_most_recently_opened_stream_and_forgets_it_with_the_last_close() -> None:
    registry = ConnectionRegistry()
    old, new = ("127.0.0.1", 45900), ("127.0.0.1", 48106)

    registry.stream_opened("session", client_address=old)
    registry.stream_opened("session", client_address=new)
    registry.stream_closed("session", client_address=old)
    presence = registry.presence("session")

    assert presence.client_address == new

    registry.stream_closed("session", client_address=new)

    assert presence.client_address is None


def test_a_stream_without_an_address_still_counts_and_names_nobody() -> None:
    registry = ConnectionRegistry()

    registry.stream_opened("session")
    presence = registry.presence("session")

    assert presence.connected
    assert presence.client_address is None


def test_presence_starts_once_and_process_binding_keeps_it_connected() -> None:
    registry = ConnectionRegistry()

    first = registry.presence("stdio", now=5)
    registry.bind_to_process("stdio")

    assert registry.presence("stdio", now=99) is first
    assert first.first_seen_at == 5
    assert first.connected


def test_prune_keeps_connected_and_leased_sessions_only() -> None:
    registry = ConnectionRegistry()
    registry.stream_opened("open")
    registry.stream_opened("closed")
    registry.stream_closed("closed")
    registry.presence("leased")
    registry.bind_to_process("stdio")

    registry.prune(keep={"leased"})

    assert "open" in registry
    assert "leased" in registry
    assert "stdio" in registry
    assert "closed" not in registry
