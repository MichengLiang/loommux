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
CONTROL_URL_ENV = "LOOMMUX_CONTROL_URL"
IDENTITY_ENVIRONMENT = (KERNEL_ID_ENV, KERNEL_NAME_ENV, CONTROL_URL_ENV)

_WILDCARD_HOSTS = {"", "0.0.0.0", "::", "[::]"}


@dataclass(frozen=True)
class KernelIdentity:
    """One kernel resource as its own kernel sees it.

    ``control_url`` is ``None`` for a daemon that serves no HTTP control
    plane, which is every stdio daemon.
    """

    kernel_id: str
    name: str
    control_url: str | None

    def environment(self) -> dict[str, str]:
        environment = {KERNEL_ID_ENV: self.kernel_id, KERNEL_NAME_ENV: self.name}
        if self.control_url is not None:
            environment[CONTROL_URL_ENV] = self.control_url
        return environment

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> KernelIdentity | None:
        values = os.environ if environ is None else environ
        kernel_id = values.get(KERNEL_ID_ENV)
        if not kernel_id:
            return None
        return cls(kernel_id, values.get(KERNEL_NAME_ENV, ""), values.get(CONTROL_URL_ENV) or None)


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
