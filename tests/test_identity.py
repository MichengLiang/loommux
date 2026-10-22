from __future__ import annotations

import pytest

from loommux.identity import CONTROL_URL_ENV, KERNEL_ID_ENV, KERNEL_NAME_ENV, KernelIdentity, loopback_control_url


def test_identity_round_trips_through_the_launch_environment() -> None:
    identity = KernelIdentity("abc123", "build", "http://127.0.0.1:8801")

    environment = identity.environment()

    assert environment == {KERNEL_ID_ENV: "abc123", KERNEL_NAME_ENV: "build", CONTROL_URL_ENV: "http://127.0.0.1:8801"}
    assert KernelIdentity.from_environ(environment) == identity
    assert identity.names == {"abc123", "build"}


def test_a_stdio_kernel_has_an_identity_without_a_control_url() -> None:
    identity = KernelIdentity("abc123", "private-1a2b3c4d", None)

    assert CONTROL_URL_ENV not in identity.environment()
    assert KernelIdentity.from_environ(identity.environment()) == identity


def test_a_process_without_a_kernel_id_is_not_a_kernel() -> None:
    assert KernelIdentity.from_environ({CONTROL_URL_ENV: "http://127.0.0.1:8801"}) is None


@pytest.mark.parametrize(
    ("host", "port", "url"),
    [
        ("127.0.0.1", 8801, "http://127.0.0.1:8801"),
        ("0.0.0.0", 8801, "http://127.0.0.1:8801"),
        ("::", 9000, "http://127.0.0.1:9000"),
        ("localhost", 9000, "http://localhost:9000"),
        ("::1", 9000, "http://[::1]:9000"),
        ("[::1]", 9000, "http://[::1]:9000"),
    ],
)
def test_a_wildcard_bind_address_is_published_as_loopback(host: str, port: int, url: str) -> None:
    assert loopback_control_url(host, port) == url
