from __future__ import annotations

import pytest

from loommux.adapter import IPythonMCPAdapter
from loommux.cell_control import LoommuxMagicError, parse_loommux_cell_control


@pytest.mark.parametrize(
    ("source", "wait_seconds", "full_output", "magic"),
    [
        ("print('ordinary')", 10.0, False, None),
        ("%%loommux\nprint('bare')", 10.0, False, "%%loommux"),
        ("%%loommux --wait 120\nprint('wait')", 120.0, False, "%%loommux --wait 120"),
        ("%%loommux --wait 0.5\nprint('fraction')", 0.5, False, "%%loommux --wait 0.5"),
        ("%%loommux --full-output\nprint('full')", 10.0, True, "%%loommux --full-output"),
        ("%%loommux --wait 2 --full-output\nprint('both')", 2.0, True, "%%loommux --wait 2 --full-output"),
    ],
)
def test_parser_resolves_every_canonical_cell_form(source: str, wait_seconds: float, full_output: bool, magic: str | None) -> None:
    parsed = parse_loommux_cell_control(source)

    assert parsed.initial_wait_seconds == wait_seconds
    assert parsed.full_output_requested is full_output
    assert parsed.control_magic == magic


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("%%loommux --wait\npass", "--wait requires one positive finite decimal value"),
        ("%%loommux --wait 0\npass", "--wait requires one positive finite decimal value"),
        ("%%loommux --wait -1\npass", "invalid --wait value '-1'"),
        ("%%loommux --wait infinity\npass", "invalid --wait value 'infinity'"),
        ("%%loommux --unknown\npass", "unknown option '--unknown'"),
        ("%%loommux --wait 20 --wait 30\npass", "--wait may be specified at most once"),
        ("%%loommux --full-output --full-output\npass", "--full-output may be specified at most once"),
        ("%%loommux  --wait 1\npass", "options must be separated by one space"),
        ("%%loommux --wait 1 \npass", "options must be separated by one space"),
        ("%%loommux\t--wait 1\npass", "%%loommux must be followed by options separated by spaces"),
    ],
)
def test_parser_rejects_each_ambiguous_or_invalid_control_declaration(source: str, message: str) -> None:
    with pytest.raises(LoommuxMagicError, match=message):
        parse_loommux_cell_control(source)


def test_magic_shaped_text_inside_an_ordinary_python_string_is_data() -> None:
    source = 'payload = """\n%%loommux --full-output\n"""\nprint(payload)'

    parsed = parse_loommux_cell_control(source)

    assert parsed.full_output_requested is False
    assert parsed.control_magic is None


def test_parser_rejects_a_decimal_that_overflows_to_infinity() -> None:
    source = f"%%loommux --wait {'9' * 400}\npass"

    with pytest.raises(LoommuxMagicError, match="positive finite decimal"):
        parse_loommux_cell_control(source)


def test_run_python_passes_original_magic_source_and_resolved_policy_to_submission() -> None:
    class CapturingAdapter(IPythonMCPAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[tuple[str, float, bool, str | None]] = []

        def _submit_python_cell(self, code: str, timeout_seconds: float, full_output_requested: bool = False, *, control_magic: str | None = None) -> dict[str, object]:
            self.calls.append((code, timeout_seconds, full_output_requested, control_magic))
            return {"ok": True, "status": "captured"}

    adapter = CapturingAdapter()
    source = "%%loommux --wait 2 --full-output\nprint('unchanged')"

    assert adapter.run_python(source)["status"] == "captured"
    assert adapter.calls == [(source, 2.0, True, "%%loommux --wait 2 --full-output")]


def test_invalid_magic_does_not_allocate_or_submit_an_execution() -> None:
    class CapturingAdapter(IPythonMCPAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.submitted = False

        def _submit_python_cell(self, *args: object, **kwargs: object) -> dict[str, object]:
            self.submitted = True
            return {"ok": True}

    adapter = CapturingAdapter()
    response = adapter.run_python("%%loommux --wait 10 --wait 20\nprint('must not run')")

    assert response == {
        "ok": False,
        "status": "invalid_loommux_magic",
        "message": "invalid_loommux_magic: --wait may be specified at most once",
    }
    assert adapter.submitted is False
    assert adapter.executions == {}
