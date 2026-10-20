from __future__ import annotations

import pytest

from loommux.session import IPythonSession
from loommux.submission import CellHeader, CellHeaderError, prepare_run_cell, read_cell_header
from loommux.submission.header import header_arguments


@pytest.mark.parametrize(
    ("source", "header"),
    [
        ("print('ordinary')", CellHeader()),
        ("", CellHeader()),
        ("%%loommux\nprint('bare')", CellHeader()),
        ("%%loommux --wait 120\nprint('wait')", CellHeader(wait_seconds=120.0)),
        ("%%loommux --wait 0.5\nprint('fraction')", CellHeader(wait_seconds=0.5)),
        ("%%loommux --full-output\nprint('full')", CellHeader(full_output=True)),
        ("%%loommux --wait 2 --full-output\nprint('both')", CellHeader(2.0, True)),
        ("%%loommux  --full-output   --wait=3\nprint('argparse spacing')", CellHeader(3.0, True)),
        ("%%loommux --wait 2\n%%bash\necho shell", CellHeader(wait_seconds=2.0)),
        ("%%loommux --wait 2\r\nprint('crlf')\r\n", CellHeader(wait_seconds=2.0)),
        ("%%loommux --wait 2", CellHeader(wait_seconds=2.0)),
        ("%%loommux --kernel build\nmake()", CellHeader(kernel="build")),
        ("%%loommux --wait 5 --kernel=build --full-output\nmake()", CellHeader(5.0, True, "build")),
        ("%%loommux --kernel 'my lab'\nmake()", CellHeader(kernel="my lab")),
    ],
)
def test_header_on_the_first_line_resolves_the_observation_policy(source: str, header: CellHeader) -> None:
    assert read_cell_header(source) == header


def test_blank_lines_and_indentation_before_the_header_follow_ipython() -> None:
    # IPython drops leading blank lines and the common indentation before it
    # looks for a cell magic; the daemon must find the same first line.
    assert read_cell_header("\n\n   \n  %%loommux --wait 4\n  x = 1\n") == CellHeader(wait_seconds=4.0)


@pytest.mark.parametrize(
    "source",
    [
        "x = 1\n%%loommux --wait 5\nprint(x)",
        'payload = """\n%%loommux --wait 0\n"""',
        "%%loommux?",
        "%%loommuxed --wait 0\npass",
        "%loommux --wait 0",
        "# %%loommux --wait 0\npass",
    ],
)
def test_only_a_first_line_header_is_read(source: str) -> None:
    assert read_cell_header(source) == CellHeader()


@pytest.mark.parametrize(
    ("line", "arguments"),
    [
        ("%%loommux", ""),
        ("%%loommux\n", ""),
        ("%%loommux --wait 3\n", "--wait 3"),
        ("%%loommux\t--full-output", "--full-output"),
        ("%%loommux?", None),
        ("%%loommuxx", None),
        ("print()", None),
    ],
)
def test_header_arguments_recognize_the_magic_name_as_a_whole_word(line: str, arguments: str | None) -> None:
    assert header_arguments(line) == arguments


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("%%loommux --wait\npass", "expected one argument"),
        ("%%loommux --wait 0\npass", "got '0'"),
        ("%%loommux --wait -1\npass", "got '-1'"),
        ("%%loommux --wait=-1\npass", "got '-1'"),
        ("%%loommux --wait inf\npass", "got 'inf'"),
        ("%%loommux --wait nan\npass", "got 'nan'"),
        ("%%loommux --wait soon\npass", "got 'soon'"),
        (f"%%loommux --wait {'9' * 400}\npass", "positive finite"),
        ("%%loommux --unknown\npass", "unrecognized arguments: --unknown"),
        # A bare word is never a kernel name: the slip `full-output` is an error.
        ("%%loommux full-output\npass", "unrecognized arguments: full-output"),
        ("%%loommux build\npass", "unrecognized arguments: build"),
        ("%%loommux --wai 5\npass", "unrecognized arguments: --wai 5"),
        ("%%loommux --full\npass", "unrecognized arguments: --full"),
        ("%%loommux --kernel\npass", "argument --kernel: expected one argument"),
        ("%%loommux --kernel 'a\tb'\npass", "kernel name must contain printable characters only"),
        (f"%%loommux --kernel {'k' * 257}\npass", "kernel name must not exceed 256 characters"),
        ("%%loommux --help\npass", "unrecognized arguments: --help"),
        ("%%loommux --wait '3\npass", "No closing quotation"),
    ],
)
def test_invalid_header_is_an_error_value_not_a_process_exit(source: str, message: str) -> None:
    with pytest.raises(CellHeaderError, match=message):
        read_cell_header(source)


def test_preparation_keeps_the_header_in_the_kernel_source() -> None:
    source = "%%loommux --wait 2 --full-output\r\n%%bash\r\nprintf 'ok\\n'\r\n"

    prepared = prepare_run_cell(source)

    assert prepared.kernel_source == source
    assert prepared.initial_wait_seconds == 2.0
    assert prepared.full_output_requested is True


def test_preparation_converts_an_apply_patch_literal_below_the_header() -> None:
    source = '''%%loommux --full-output
payload = r"""
*** Begin Patch
*** Add File: captured.txt
+contents
*** End Patch
"""
payload
'''

    prepared = prepare_run_cell(source)

    assert prepared.kernel_source.startswith("%%loommux --full-output\npayload = ")
    assert 'r"""' not in prepared.kernel_source
    assert prepared.full_output_requested is True


def test_run_cell_passes_the_unchanged_source_and_resolved_policy_to_submission() -> None:
    class CapturingSession(IPythonSession):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[tuple[str, float, bool]] = []

        def _submit_python_cell(self, source: str, timeout_seconds: float, full_output_requested: bool = False) -> dict[str, object]:
            self.calls.append((source, timeout_seconds, full_output_requested))
            return {"ok": True, "status": "captured"}

    session = CapturingSession()
    source = "%%loommux --wait 2 --full-output\nprint('kept')"

    assert session.run_cell(source)["status"] == "captured"
    assert session.calls == [(source, 2.0, True)]


def test_invalid_header_does_not_allocate_or_submit_an_execution() -> None:
    class CapturingSession(IPythonSession):
        def __init__(self) -> None:
            super().__init__()
            self.submitted = False

        def _submit_python_cell(self, *args: object, **kwargs: object) -> dict[str, object]:
            self.submitted = True
            return {"ok": True}

    session = CapturingSession()
    response = session.run_cell("%%loommux --wait 10 --later\nprint('must not run')")

    assert response == {
        "ok": False,
        "status": "invalid_cell_header",
        "message": "invalid %%loommux header: unrecognized arguments: --later",
    }
    assert session.submitted is False
    assert session.executions == {}
