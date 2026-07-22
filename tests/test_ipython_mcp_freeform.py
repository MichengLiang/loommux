from __future__ import annotations

import pytest

from loommux.adapter import IPythonMCPAdapter
from loommux.cell_control import LoommuxDirectiveError, parse_loommux_cell_control


@pytest.mark.parametrize(
    ("source", "wait_seconds", "full_output", "directives"),
    [
        ("print('ordinary')", 10.0, False, ()),
        ("# loommux: --wait 120\nprint('wait')", 120.0, False, ("# loommux: --wait 120",)),
        ("# loommux: --full-output\nprint('full')", 10.0, True, ("# loommux: --full-output",)),
        ("# loommux: --wait 2 --full-output\nprint('both')", 2.0, True, ("# loommux: --wait 2 --full-output",)),
        ("# loommux: --wait 2\n# loommux: --full-output\nprint('split')", 2.0, True, ("# loommux: --wait 2", "# loommux: --full-output")),
        ("%%bash\n# loommux: --wait 0.5\necho shell", 0.5, False, ("# loommux: --wait 0.5",)),
    ],
)
def test_parser_resolves_every_canonical_directive_form(source: str, wait_seconds: float, full_output: bool, directives: tuple[str, ...]) -> None:
    parsed = parse_loommux_cell_control(source)

    assert parsed.initial_wait_seconds == wait_seconds
    assert parsed.full_output_requested is full_output
    assert parsed.control_directives == directives


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("# loommux:\npass", "# loommux: requires at least one option"),
        ("# loommux: --wait\npass", "--wait requires one positive finite decimal value"),
        ("# loommux: --wait 0\npass", "--wait requires one positive finite decimal value"),
        ("# loommux: --wait -1\npass", "invalid --wait value '-1'"),
        ("# loommux: --wait infinity\npass", "invalid --wait value 'infinity'"),
        ("# loommux: --unknown\npass", "unknown option '--unknown'"),
        ("# loommux: --wait 20 --wait 30\npass", "--wait may be specified at most once"),
        ("# loommux: --full-output --full-output\npass", "--full-output may be specified at most once"),
        ("# loommux: --wait 20\n# loommux: --wait 30\npass", "--wait may be specified at most once"),
        ("# loommux: --wait 1\n# loommux: --full-output\n# loommux: --full-output\npass", "--full-output may be specified at most once"),
        ("# loommux:  --wait 1\npass", "options must be separated by one space"),
        ("# loommux: --wait 1 \npass", "options must be separated by one space"),
        ("# loommux:\t--wait 1\npass", "options must be separated by one space"),
    ],
)
def test_parser_rejects_each_ambiguous_or_invalid_control_declaration(source: str, message: str) -> None:
    with pytest.raises(LoommuxDirectiveError, match=message):
        parse_loommux_cell_control(source)


def test_directive_shaped_text_inside_an_ordinary_python_string_is_data() -> None:
    source = 'payload = """\n# loommux: --full-output\n"""\nprint(payload)'

    parsed = parse_loommux_cell_control(source)

    assert parsed.full_output_requested is False
    assert parsed.control_directives == ()


def test_parser_rejects_a_decimal_that_overflows_to_infinity() -> None:
    source = f"# loommux: --wait {'9' * 400}\npass"

    with pytest.raises(LoommuxDirectiveError, match="positive finite decimal"):
        parse_loommux_cell_control(source)


def test_run_python_passes_original_directive_source_and_resolved_policy_to_submission() -> None:
    class CapturingAdapter(IPythonMCPAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[tuple[str, float, bool, tuple[str, ...]]] = []

        def _submit_python_cell(self, code: str, timeout_seconds: float, full_output_requested: bool = False, *, control_directives: tuple[str, ...] = ()) -> dict[str, object]:
            self.calls.append((code, timeout_seconds, full_output_requested, control_directives))
            return {"ok": True, "status": "captured"}

    adapter = CapturingAdapter()
    source = "# loommux: --wait 2\n# loommux: --full-output\nprint('unchanged')"

    assert adapter.run_python(source)["status"] == "captured"
    assert adapter.calls == [(source, 2.0, True, ("# loommux: --wait 2", "# loommux: --full-output"))]


def test_invalid_directive_does_not_allocate_or_submit_an_execution() -> None:
    class CapturingAdapter(IPythonMCPAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.submitted = False

        def _submit_python_cell(self, *args: object, **kwargs: object) -> dict[str, object]:
            self.submitted = True
            return {"ok": True}

    adapter = CapturingAdapter()
    response = adapter.run_python("# loommux: --wait 10\n# loommux: --wait 20\nprint('must not run')")

    assert response == {
        "ok": False,
        "status": "invalid_loommux_directive",
        "message": "invalid_loommux_directive: --wait may be specified at most once",
    }
    assert adapter.submitted is False
    assert adapter.executions == {}
