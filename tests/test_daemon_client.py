from __future__ import annotations

import socket
import time

import pytest

from loommux.daemon import Daemon, DaemonError


def test_kernels_gives_up_after_its_timeout_on_a_daemon_that_never_answers() -> None:
    with socket.socket() as silent:
        # Listening without ever accepting: the connection is queued and the
        # request is never answered, as with a daemon whose event loop is stuck.
        silent.bind(("127.0.0.1", 0))
        silent.listen()
        daemon = Daemon(f"http://127.0.0.1:{silent.getsockname()[1]}")
        started = time.monotonic()
        with pytest.raises(DaemonError, match="cannot reach"):
            daemon.kernels(timeout=0.3)
        assert time.monotonic() - started < 5
        daemon.close()
