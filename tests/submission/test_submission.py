from __future__ import annotations

import pytest

from loommux.session import IPythonSession, prepare_run_cell
from loommux.submission.directives import LoommuxDirectiveError, remove_active_directive_lines, scan_active_loommux_directives


@pytest.mark.parametrize(
    ("source", "wait_seconds", "full_output", "clean_source"),
    [
        ("print('ordinary')", 10.0, False, "print('ordinary')"),
        ("# loommux: --wait 120\nprint('wait')", 120.0, False, "print('wait')"),
        ("# loommux: --full-output\nprint('full')", 10.0, True, "print('full')"),
        ("# loommux: --wait 2 --full-output\nprint('both')", 2.0, True, "print('both')"),
        ("# loommux: --wait 2\n# loommux: --full-output\nprint('split')", 2.0, True, "print('split')"),
        ("# loommux: --wait 0.5\n%%bash\necho shell", 0.5, False, "%%bash\necho shell"),
        ("%%bash\n# loommux: --wait 0.5\necho shell", 0.5, False, "%%bash\necho shell"),
    ],
)
def test_scan_resolves_policy_and_removes_exact_active_lines(source: str, wait_seconds: float, full_output: bool, clean_source: str) -> None:
    scan = scan_active_loommux_directives(source)

    assert scan.initial_wait_seconds == wait_seconds
    assert scan.full_output_requested is full_output
    assert remove_active_directive_lines(source, scan.active_directive_ranges) == clean_source


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
        ("# loommux:  --wait 1\npass", "options must be separated by one space"),
        ("# loommux: --wait 1 \npass", "options must be separated by one space"),
        ("# loommux:\t--wait 1\npass", "options must be separated by one space"),
    ],
)
def test_scan_rejects_each_ambiguous_or_invalid_control_declaration(source: str, message: str) -> None:
    with pytest.raises(LoommuxDirectiveError, match=message):
        scan_active_loommux_directives(source)


def test_directive_shaped_text_inside_an_ordinary_python_string_is_data() -> None:
    source = 'payload = """\n# loommux: --full-output\n"""\nprint(payload)'

    scan = scan_active_loommux_directives(source)

    assert scan.full_output_requested is False
    assert scan.active_directive_ranges == ()
    assert remove_active_directive_lines(source, scan.active_directive_ranges) == source


def test_directive_shaped_text_inside_a_lone_cr_python_string_is_data() -> None:
    source = 'payload = """\r# loommux: --wait 0\r"""\rprint(payload)'

    scan = scan_active_loommux_directives(source)

    assert scan.full_output_requested is False
    assert scan.active_directive_ranges == ()
    assert remove_active_directive_lines(source, scan.active_directive_ranges) == source


@pytest.mark.parametrize("line_ending", ["\n", "\r\n", "\r"])
def test_directive_shaped_text_inside_an_f_string_is_data(line_ending: str) -> None:
    source = f'payload = f"""{line_ending}# loommux: --wait 0{line_ending}"""{line_ending}print(payload)'

    scan = scan_active_loommux_directives(source)

    assert scan.full_output_requested is False
    assert scan.active_directive_ranges == ()
    assert remove_active_directive_lines(source, scan.active_directive_ranges) == source


def test_scan_rejects_a_decimal_that_overflows_to_infinity() -> None:
    source = f"# loommux: --wait {'9' * 400}\npass"

    with pytest.raises(LoommuxDirectiveError, match="positive finite decimal"):
        scan_active_loommux_directives(source)


def test_preparation_removes_crlf_directives_without_padding() -> None:
    source = "# loommux: --wait 2\r\n# loommux: --full-output\r\n%%bash\r\nprintf 'ok\\n'\r\n"

    prepared = prepare_run_cell(source)

    assert prepared.kernel_source == "%%bash\r\nprintf 'ok\\n'\r\n"
    assert prepared.initial_wait_seconds == 2.0
    assert prepared.full_output_requested is True


def test_preparation_deletes_a_final_directive_without_a_terminator() -> None:
    source = "print('before')\n# loommux: --full-output"

    prepared = prepare_run_cell(source)

    assert prepared.kernel_source == "print('before')\n"
    assert prepared.full_output_requested is True


def test_preparation_removes_outer_directives_before_apply_patch_conversion() -> None:
    source = '''# loommux: --full-output
payload = r"""
*** Begin Patch
*** Add File: captured.txt
+contents
*** End Patch
"""
payload
'''

    prepared = prepare_run_cell(source)

    assert "# loommux:" not in prepared.kernel_source
    assert "*** Begin Patch" in prepared.kernel_source
    assert prepared.full_output_requested is True


def test_run_cell_passes_clean_source_and_resolved_policy_to_submission() -> None:
    class CapturingSession(IPythonSession):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[tuple[str, float, bool]] = []

        def _submit_python_cell(self, source: str, timeout_seconds: float, full_output_requested: bool = False) -> dict[str, object]:
            self.calls.append((source, timeout_seconds, full_output_requested))
            return {"ok": True, "status": "captured"}

    session = CapturingSession()
    source = "# loommux: --wait 2\n# loommux: --full-output\nprint('clean')"

    assert session.run_cell(source)["status"] == "captured"
    assert session.calls == [("print('clean')", 2.0, True)]


def test_invalid_directive_does_not_allocate_or_submit_an_execution() -> None:
    class CapturingSession(IPythonSession):
        def __init__(self) -> None:
            super().__init__()
            self.submitted = False

        def _submit_python_cell(self, *args: object, **kwargs: object) -> dict[str, object]:
            self.submitted = True
            return {"ok": True}

    session = CapturingSession()
    response = session.run_cell("# loommux: --wait 10\n# loommux: --wait 20\nprint('must not run')")

    assert response == {
        "ok": False,
        "status": "invalid_loommux_directive",
        "message": "invalid_loommux_directive: --wait may be specified at most once",
    }
    assert session.submitted is False
    assert session.executions == {}
