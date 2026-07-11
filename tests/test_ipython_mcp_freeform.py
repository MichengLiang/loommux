from __future__ import annotations

import pytest

from loommux.adapter import IPythonMCPAdapter, parse_run_python_freeform_timeout, parse_run_python_full_output


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("", (10.0, "default")),
        ("# loommux: timeout_seconds=120", (120.0, "directive")),
        ("# loommux: timeout_seconds=0.5", (0.5, "directive")),
        ("# loommux: timeout_seconds=0", (10.0, "default")),
        ("# loommux: timeout_seconds=01", (10.0, "default")),
        ("# loommux: timeout_seconds = 1", (10.0, "default")),
        ("# loommux: timeout_seconds=1\n# loommux: timeout_seconds=2", (10.0, "default")),
    ],
)
def test_timeout_directive_uses_exactly_one_complete_line(source: str, expected: tuple[float, str]) -> None:
    assert parse_run_python_freeform_timeout(source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("", False),
        ("# loommux: full_output", True),
        ("# loommux: full_output\n# loommux: full_output", True),
        (" # loommux: full_output", False),
        ("# loommux: full_output ", False),
        ("# loommux: full_output=yes", False),
        ("# loommux: FULL_OUTPUT", False),
    ],
)
def test_full_output_directive_requires_an_exact_complete_line(source: str, expected: bool) -> None:
    assert parse_run_python_full_output(source) is expected


def test_run_python_passes_original_freeform_source_to_submission() -> None:
    class CapturingAdapter(IPythonMCPAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[tuple[str, float, bool]] = []

        def _submit_python_cell(self, code: str, timeout_seconds: float, full_output_requested: bool = False):
            self.calls.append((code, timeout_seconds, full_output_requested))
            return {"ok": True, "status": "captured"}

    adapter = CapturingAdapter()
    source = "# loommux: timeout_seconds=2\n# loommux: full_output\nprint('unchanged')"
    assert adapter.run_python(source)["status"] == "captured"
    assert adapter.calls == [(source, 2.0, True)]
