"""Compare result text without the duration measured on its first line."""

import re

_DURATION = re.compile(r"^In \[(\d+)\] · \d+(?:\.\d)? m?s:")


def untimed(text: str) -> str:
    """``In [N] · 54.3 s:`` read as ``In [N]:``: a test about output or binding does not assert a measured time."""
    return _DURATION.sub(r"In [\1]:", text, count=1)
