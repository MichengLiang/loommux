from __future__ import annotations

from typing import Any

import pytest

from loommux import kernel_extension
from loommux.kernel_extension import remove_own_header


@pytest.mark.parametrize(
    ("lines", "transformed"),
    [
        (["x = 1\n"], ["x = 1\n"]),
        ([], []),
        (["%%loommux --wait 2\n", "x = 1\n"], ["\n", "x = 1\n"]),
        (["%%loommux\n", "%%loommux --full-output\n", "x\n"], ["\n", "\n", "x\n"]),
        (["%%loommux --wait 2\n", "%%bash\n", "echo\n"], ["%%bash\n", "echo\n"]),
        (["%%loommux --wait 2\n"], ["\n"]),
        (["%%loommux --bogus\n", "x\n"], ["%%loommux --bogus\n", "x\n"]),
        (["%%loommux\n", "%%loommux --bogus\n", "x\n"], ["%%loommux --bogus\n", "x\n"]),
        (["%%loommux?\n"], ["%%loommux?\n"]),
    ],
)
def test_headers_naming_this_kernel_dissolve_and_keep_line_numbers(lines: list[str], transformed: list[str]) -> None:
    assert remove_own_header(lines) == transformed


def test_transformer_is_pure_so_ipython_may_apply_it_twice() -> None:
    lines = ["%%loommux --wait 2\n", "x = 1\n"]

    remove_own_header(lines)

    assert lines == ["%%loommux --wait 2\n", "x = 1\n"]


class _Shell:
    def __init__(self) -> None:
        self.input_transformers_cleanup: list[Any] = []
        self.magics: dict[tuple[str, str], Any] = {}
        self.magics_manager = self

    def register_function(self, function: Any, kind: str, name: str) -> None:
        self.magics[kind, name] = function


def test_loading_twice_installs_one_transformer_and_unloading_removes_it() -> None:
    shell: Any = _Shell()

    kernel_extension.load_ipython_extension(shell)
    kernel_extension.load_ipython_extension(shell)

    assert shell.input_transformers_cleanup == [remove_own_header]
    assert set(shell.magics) == {("cell", "loommux"), ("line", "%loommux"), ("line", "loommux")}
    assert "--full-output" in (shell.magics["cell", "loommux"].__doc__ or "")

    kernel_extension.unload_ipython_extension(shell)
    kernel_extension.unload_ipython_extension(shell)

    assert shell.input_transformers_cleanup == []
