"""Parse the adapter-owned control declaration of one submitted cell."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

DEFAULT_INITIAL_WAIT_SECONDS = 10.0
_MAGIC_NAME = "%%loommux"
# ``0`` is admitted solely so it can receive the specified non-positive error
# rather than being indistinguishable from an arbitrary malformed token.
_DECIMAL_LITERAL_RE = re.compile(r"(?:0|[1-9][0-9]*|[0-9]+\.[0-9]+)\Z")


@dataclass(frozen=True)
class LoommuxCellControl:
    """Resolved, immutable control facts for one accepted author cell."""

    initial_wait_seconds: float
    full_output_requested: bool
    control_magic: str | None


class LoommuxMagicError(ValueError):
    """A concise, safe-to-return validation failure for ``%%loommux``."""


def parse_loommux_cell_control(author_source: str) -> LoommuxCellControl:
    """Resolve a first-line ``%%loommux`` declaration without touching the kernel.

    Only the physical first line is a control declaration. This keeps text in
    ordinary Python strings strictly data and ensures every accepted execution
    has one auditable, pre-submission policy.
    """

    first_line = author_source.splitlines(keepends=True)[0] if author_source else ""
    magic_line = first_line.rstrip("\r\n")
    if not magic_line.startswith(_MAGIC_NAME):
        return LoommuxCellControl(DEFAULT_INITIAL_WAIT_SECONDS, False, None)
    if len(magic_line) > len(_MAGIC_NAME) and magic_line[len(_MAGIC_NAME)] != " ":
        raise LoommuxMagicError("%%loommux must be followed by options separated by spaces")

    suffix = magic_line[len(_MAGIC_NAME) :]
    if not suffix:
        return LoommuxCellControl(DEFAULT_INITIAL_WAIT_SECONDS, False, magic_line)
    if suffix.startswith("  ") or suffix.endswith(" "):
        raise LoommuxMagicError("options must be separated by one space")

    tokens = suffix[1:].split(" ")
    wait_seconds = DEFAULT_INITIAL_WAIT_SECONDS
    full_output_requested = False
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--wait":
            if wait_seconds != DEFAULT_INITIAL_WAIT_SECONDS or "--wait" in tokens[:index]:
                raise LoommuxMagicError("--wait may be specified at most once")
            if index + 1 >= len(tokens):
                raise LoommuxMagicError("--wait requires one positive finite decimal value")
            value = tokens[index + 1]
            if _DECIMAL_LITERAL_RE.fullmatch(value) is None:
                raise LoommuxMagicError(f"invalid --wait value {value!r}")
            parsed = float(value)
            if not math.isfinite(parsed) or parsed <= 0:
                raise LoommuxMagicError("--wait requires one positive finite decimal value")
            wait_seconds = parsed
            index += 2
        elif token == "--full-output":
            if full_output_requested:
                raise LoommuxMagicError("--full-output may be specified at most once")
            full_output_requested = True
            index += 1
        else:
            raise LoommuxMagicError(f"unknown option {token!r}")
    return LoommuxCellControl(wait_seconds, full_output_requested, magic_line)
