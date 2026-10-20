from __future__ import annotations

import pytest

from loommux.host_workspace_config import WORKSPACE_CONFIG_ENV
from loommux.identity import IDENTITY_ENVIRONMENT


@pytest.fixture(autouse=True)
def isolate_workspace_resolver_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests independent of the MCP host's workspace authorization."""
    # A real host may set this resolver before launching pytest. Tests that need
    # an explicit resolver set it themselves; all other tests must exercise the
    # launch-directory default regardless of their parent process environment.
    monkeypatch.delenv(WORKSPACE_CONFIG_ENV, raising=False)


@pytest.fixture(autouse=True)
def isolate_kernel_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a test process from believing it is the kernel that launched pytest."""
    for name in IDENTITY_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
