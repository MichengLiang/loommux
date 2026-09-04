from unittest.mock import patch

import pytest

from loommux.resource.routing import (
    ResourceRoutingError,
    resolve_address,
    resolve_client,
)


class FakeContext:
    def __init__(self, session_id: str | None) -> None:
        self.session_id = session_id


def test_session_identity_selects_a_private_resource() -> None:
    context = FakeContext("session-123456789")

    with patch("loommux.resource.routing.get_http_headers", return_value={}):
        address = resolve_address(context)
        client = resolve_client(context)

    assert address.key == "session:session-123456789"
    assert address.scope == "session_private"
    assert not address.shared
    assert client.client_id == "session-123456789"


def test_named_header_selects_a_shared_resource_and_decodes_labels() -> None:
    context = FakeContext("session-a")

    with patch(
        "loommux.resource.routing.get_http_headers",
        return_value={
            "x-loommux-resource": "%E5%85%B1%E4%BA%AB%E5%AE%9E%E9%AA%8C%E5%AE%A4",
            "x-loommux-operator": "%E6%9E%97%E5%B0%8F%E6%BB%A1",
        },
    ):
        address = resolve_address(context)
        client = resolve_client(context)

    assert address.key == "named:共享实验室"
    assert address.display_name == "共享实验室"
    assert address.shared
    assert client.display_name == "林小满"


def test_missing_session_identity_is_rejected() -> None:
    context = FakeContext(None)

    with patch("loommux.resource.routing.get_http_headers", return_value={}):
        with pytest.raises(ResourceRoutingError):
            resolve_address(context)
