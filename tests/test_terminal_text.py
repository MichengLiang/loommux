from __future__ import annotations

import pytest

from loommux.terminal_text import TerminalTextNormalizer


@pytest.mark.parametrize(
    "chunks",
    [
        ("before\x1b", "[31mred\x1b[0mafter"),
        ("before\x1b[3", "1mred\x1b[0mafter"),
        ("before\x1b[31mred\x1b[", "0mafter"),
    ],
)
def test_csi_sequences_leave_no_split_control_fragments(chunks: tuple[str, str]) -> None:
    normalizer = TerminalTextNormalizer()

    first, second = (normalizer.normalize(chunk) for chunk in chunks)

    assert first + second == "beforeredafter"
    assert "\x1b" not in first + second


def test_osc_bel_and_st_sequences_are_removed_across_boundaries() -> None:
    bel = TerminalTextNormalizer()
    st = TerminalTextNormalizer()

    bel_text = bel.normalize("before\x1b]0;window title") + bel.normalize("\x07after")
    st_text = st.normalize("before\x1b]8;;https://example.test\x1b") + st.normalize("\\link\x1b]8;;\x1b\\after")

    assert bel_text == "beforeafter"
    assert st_text == "beforelinkafter"
    assert "\x1b" not in bel_text + st_text
    assert "\x07" not in bel_text + st_text


def test_stream_normalizers_keep_state_separate_and_preserve_unicode_newlines() -> None:
    stdout = TerminalTextNormalizer()
    stderr = TerminalTextNormalizer()

    stdout_first = stdout.normalize("stdout \x1b[3")
    stderr_text = stderr.normalize("stderr\n中文\ttext")
    stdout_second = stdout.normalize("1mvisible\x1b[0m")

    assert stdout_first + stdout_second == "stdout visible"
    assert stderr_text == "stderr\n中文\ttext"
    assert "\x1b" not in stdout_first + stdout_second + stderr_text


def test_cursor_and_c1_terminal_controls_do_not_enter_the_transcript() -> None:
    normalizer = TerminalTextNormalizer()

    text = normalizer.normalize("left\x1b[2Kright\x1b(0charset\x9b31mred\x9b0m\x9dtitle\x9cend")

    assert text == "leftrightcharsetredend"
    assert not any(ord(character) in range(0x7F, 0xA0) for character in text)
