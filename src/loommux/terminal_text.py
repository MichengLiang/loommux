"""Stateful normalization for terminal-formatted IOPub text."""

from __future__ import annotations

from enum import Enum, auto


class _ParserState(Enum):
    TEXT = auto()
    ESC = auto()
    ESC_INTERMEDIATE = auto()
    CSI = auto()
    OSC = auto()
    OSC_ESC = auto()
    STRING = auto()
    STRING_ESC = auto()


class TerminalTextNormalizer:
    """Remove terminal controls while preserving ordinary text across chunks."""

    def __init__(self) -> None:
        self._state = _ParserState.TEXT

    def normalize(self, text: str) -> str:
        """Return this chunk's text projection and retain unfinished control state."""
        output: list[str] = []
        for character in text:
            self._consume(character, output)
        return "".join(output)

    def _consume(self, character: str, output: list[str]) -> None:
        # CSI and OSC controls can span IOPub chunks, so incomplete prefixes stay private.
        if self._state is _ParserState.TEXT:
            if character == "\x1b":
                self._state = _ParserState.ESC
            elif character == "\x9b":
                self._state = _ParserState.CSI
            elif character == "\x9d":
                self._state = _ParserState.OSC
            elif character in {"\x90", "\x98", "\x9e", "\x9f"}:
                self._state = _ParserState.STRING
            elif _is_plain_text(character):
                output.append(character)
            return

        if self._state is _ParserState.ESC:
            if character == "[":
                self._state = _ParserState.CSI
            elif character == "]":
                self._state = _ParserState.OSC
            elif character in {"P", "^", "_"}:
                self._state = _ParserState.STRING
            elif " " <= character <= "/":
                self._state = _ParserState.ESC_INTERMEDIATE
            else:
                self._state = _ParserState.TEXT
            return

        if self._state is _ParserState.ESC_INTERMEDIATE:
            if character == "\x1b":
                self._state = _ParserState.ESC
            elif not " " <= character <= "/":
                self._state = _ParserState.TEXT
            return

        if self._state is _ParserState.CSI:
            if "@" <= character <= "~":
                self._state = _ParserState.TEXT
            elif character == "\x1b":
                self._state = _ParserState.ESC
            elif character == "\x9c":
                self._state = _ParserState.TEXT
            return

        if self._state is _ParserState.OSC:
            if character in {"\x07", "\x9c"}:
                self._state = _ParserState.TEXT
            elif character == "\x1b":
                self._state = _ParserState.OSC_ESC
            return

        if self._state is _ParserState.OSC_ESC:
            self._state = _ParserState.TEXT if character == "\\" else _ParserState.OSC
            return

        if self._state is _ParserState.STRING:
            if character == "\x9c":
                self._state = _ParserState.TEXT
            elif character == "\x1b":
                self._state = _ParserState.STRING_ESC
            return

        self._state = _ParserState.TEXT if character == "\\" else _ParserState.STRING


def _is_plain_text(character: str) -> bool:
    codepoint = ord(character)
    return character in {"\n", "\t"} or codepoint >= 0x20 and not 0x7F <= codepoint <= 0x9F
