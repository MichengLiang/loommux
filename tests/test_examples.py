from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).parents[1] / "examples"


@pytest.mark.skipif(sys.platform == "win32", reason="the tour drives a Streamable HTTP subprocess, Linux-only in this suite")
@pytest.mark.timeout(120)
def test_the_kernel_environment_tour_sees_what_it_shows() -> None:
    # The tour checks every step itself; it exits non-zero when one does not see what it expects.
    completed = subprocess.run([sys.executable, str(EXAMPLES / "kernel-environment" / "tour.py")], capture_output=True, text=True, timeout=110, check=False)

    assert completed.returncode == 0, completed.stdout + completed.stderr
