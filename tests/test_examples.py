from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).parents[1] / "examples"
TUTORIAL = Path(__file__).parents[1] / "docs" / "kernel-environment-tutorial.md"


@pytest.mark.skipif(sys.platform == "win32", reason="the tour drives a Streamable HTTP subprocess, Linux-only in this suite")
@pytest.mark.timeout(120)
def test_the_kernel_environment_tour_sees_what_it_shows() -> None:
    # The tour checks every step itself; it exits non-zero when one does not see what it expects.
    completed = subprocess.run([sys.executable, str(EXAMPLES / "kernel-environment" / "tour.py")], capture_output=True, text=True, timeout=110, check=False)

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_the_tutorial_teaches_the_example_startup_files_as_they_are() -> None:
    # The tutorial explains these files line by line and the tour runs them:
    # one text in two places, so a machine keeps them equal.
    blocks = re.findall(r"```python\n(.*?)```", TUTORIAL.read_text(encoding="utf-8"), flags=re.DOTALL)
    startup = sorted((EXAMPLES / "kernel-environment" / ".loommux" / "startup").glob("*.py"))

    assert startup
    assert [path.name for path in startup if path.read_text(encoding="utf-8") not in blocks] == []
