from unittest.mock import patch

import pytest

from loommux.resource import KernelRequest, LeaseClient, RequestMode
from loommux.resource.routing import (
    ResourceRoutingError,
    connection_request,
    private_kernel_name,
    resolve_client,
    resolve_policy_generation,
)


class FakeContext:
    def __init__(self, session_id: str | None) -> None:
        self.session_id = session_id


def test_a_session_without_a_connection_header_requests_nothing() -> None:
    context = FakeContext("session-123456789")

    with patch("loommux.resource.routing.get_http_headers", return_value={}):
        request = connection_request()
        client = resolve_client(context)

    assert request is None
    assert client.client_id == "session-123456789"
    assert private_kernel_name(client) == "private-session-"
    assert private_kernel_name(LeaseClient("0123456789abcdef", "x")) == "private-01234567"


def test_connection_header_joins_a_named_main_kernel_and_decodes_labels() -> None:
    context = FakeContext("session-a")

    with patch(
        "loommux.resource.routing.get_http_headers",
        return_value={
            "x-loommux-resource": "%E5%85%B1%E4%BA%AB%E5%AE%9E%E9%AA%8C%E5%AE%A4",
            "x-loommux-operator": "%E6%9E%97%E5%B0%8F%E6%BB%A1",
        },
    ):
        request = connection_request()
        client = resolve_client(context)

    assert request == KernelRequest("共享实验室", RequestMode.JOIN)
    assert client.display_name == "林小满"


def test_missing_session_identity_is_rejected() -> None:
    context = FakeContext(None)

    with patch("loommux.resource.routing.get_http_headers", return_value={}):
        with pytest.raises(ResourceRoutingError):
            resolve_client(context)


def test_policy_generation_header_is_validated() -> None:
    with patch(
        "loommux.resource.routing.get_http_headers",
        return_value={"x-loommux-lease-policy-generation": "7"},
    ):
        assert resolve_policy_generation() == 7

    with patch(
        "loommux.resource.routing.get_http_headers",
        return_value={"x-loommux-lease-policy-generation": "invalid"},
    ):
        with pytest.raises(ResourceRoutingError):
            resolve_policy_generation()

    with patch("loommux.resource.routing.get_http_headers", return_value={}):
        assert resolve_policy_generation() is None


@pytest.mark.parametrize(
    "resource_name",
    ["line%0Abreak", "x" * 257],
)
def test_named_resource_labels_have_a_bounded_printable_contract(
    resource_name: str,
) -> None:
    with patch(
        "loommux.resource.routing.get_http_headers",
        return_value={"x-loommux-resource": resource_name},
    ):
        with pytest.raises(ResourceRoutingError):
            connection_request()
