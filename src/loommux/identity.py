"""Tell a kernel which resource it is and where its daemon listens.

The resource manager writes these variables into a kernel's launch
environment; the ``loommux`` client inside the kernel reads them back. Both
sides use this module so the names exist once.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

KERNEL_ID_ENV = "LOOMMUX_KERNEL_ID"
KERNEL_NAME_ENV = "LOOMMUX_KERNEL_NAME"
KERNEL_REQUESTED_NAME_ENV = "LOOMMUX_KERNEL_REQUESTED_NAME"
CONTROL_URL_ENV = "LOOMMUX_CONTROL_URL"
IDENTITY_ENVIRONMENT = (KERNEL_ID_ENV, KERNEL_NAME_ENV, KERNEL_REQUESTED_NAME_ENV, CONTROL_URL_ENV)
MAX_LABEL_LENGTH = 256

_WILDCARD_HOSTS = {"", "0.0.0.0", "::", "[::]"}


def label_problem(value: str, label: str) -> str | None:
    """Why ``value`` cannot name a kernel resource or operator, or ``None``.

    Names reach the daemon through HTTP headers, cell headers, and control
    requests alike, so all of them are judged by this one rule.
    """
    if len(value) > MAX_LABEL_LENGTH:
        return f"{label} must not exceed {MAX_LABEL_LENGTH} characters"
    if not value.isprintable():
        return f"{label} must contain printable characters only"
    return None


@dataclass(frozen=True)
class KernelIdentity:
    """One kernel resource as its own kernel sees it.

    ``control_url`` is ``None`` for a daemon that serves no HTTP control
    plane, which is every stdio daemon.

    ``requested_name`` is the name a session asked for when the kernel
    received a numbered one instead (``lab`` for ``lab-2``). The session's
    first cell still says ``--kernel lab``, and without it that cell would be
    forwarded to a new child kernel called ``lab``.
    """

    kernel_id: str
    name: str
    control_url: str | None
    requested_name: str = ""

    @property
    def names(self) -> frozenset[str]:
        """Every way a cell header may name this kernel."""
        return frozenset({self.kernel_id, self.name, self.requested_name} - {""})

    def environment(self) -> dict[str, str]:
        environment = {KERNEL_ID_ENV: self.kernel_id, KERNEL_NAME_ENV: self.name}
        if self.requested_name:
            environment[KERNEL_REQUESTED_NAME_ENV] = self.requested_name
        if self.control_url is not None:
            environment[CONTROL_URL_ENV] = self.control_url
        return environment

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> KernelIdentity | None:
        values = os.environ if environ is None else environ
        kernel_id = values.get(KERNEL_ID_ENV)
        if not kernel_id:
            return None
        return cls(kernel_id, values.get(KERNEL_NAME_ENV, ""), values.get(CONTROL_URL_ENV) or None, values.get(KERNEL_REQUESTED_NAME_ENV, ""))


def loopback_control_url(host: str, port: int) -> str:
    """The origin a kernel on this machine uses to reach an HTTP daemon.

    A wildcard bind address accepts loopback connections but is not itself
    an address to connect to, so it is published as ``127.0.0.1``.
    """
    if host in _WILDCARD_HOSTS:
        host = "127.0.0.1"
    elif ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}"
