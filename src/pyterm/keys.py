"""Keyboard -> serial-bytes mapping and the minicom-style Ctrl+A prefix model.

The mapper is pure (no textual import) so it is unit-testable; callers pass the
canonical key string (``event.key``) plus the printable character
(``event.character``).
"""

from __future__ import annotations

from pyterm.config import AppConfig


# Control byte for ctrl+<letter>
def _ctrl_code(letter: str) -> bytes:
    return bytes([max(1, min(26, ord(letter.lower()) - 96))])


_CTRL_PUNCT = {
    "@": 0x00,
    "`": 0x00,
    "2": 0x00,
    "space": 0x00,
    "[": 0x1B,
    "3": 0x1B,
    "\\": 0x1C,
    "]": 0x1D,
    "^": 0x1E,
    "6": 0x1E,
    "_": 0x1F,
    "7": 0x1F,
    "/": 0x1F,
    "?": 0x7F,
}

_VT_SEQUENCES = {
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "right": b"\x1b[C",
    "left": b"\x1b[D",
    "home": b"\x1b[H",
    "end": b"\x1b[F",
    "insert": b"\x1b[2~",
    "delete": b"\x1b[3~",
    "pageup": b"\x1b[5~",
    "pagedown": b"\x1b[6~",
    "f1": b"\x1bOP",
    "f2": b"\x1bOQ",
    "f3": b"\x1bOR",
    "f4": b"\x1bOS",
    "f5": b"\x1b[15~",
    "f6": b"\x1b[17~",
    "f7": b"\x1b[18~",
    "f8": b"\x1b[19~",
    "f9": b"\x1b[20~",
    "f10": b"\x1b[21~",
    "f11": b"\x1b[23~",
    "f12": b"\x1b[24~",
}


class KeyMapper:
    """Translate a textual key event into bytes to send over the wire."""

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg

    def refresh(self, cfg: AppConfig) -> None:
        self.cfg = cfg

    def enter_bytes(self) -> bytes:
        mode = self.cfg.enter_sends
        return {"cr": b"\r", "crlf": b"\r\n", "lf": b"\n", "none": b""}.get(mode, b"\r")

    def backspace_bytes(self) -> bytes:
        return b"\x7f" if self.cfg.backspace_sends == "del" else b"\x08"

    def map(self, key: str, character: str | None) -> bytes | None:
        """Return bytes to transmit, or ``None`` when the key must not be sent."""
        # printable single character (includes unicode from IME etc.)
        if character is not None and len(character) == 1 and character.isprintable():
            return character.encode("utf-8")

        parts = key.split("+")
        base = parts[-1]

        if "ctrl" in parts:
            if len(base) == 1 and base.isalpha():
                return _ctrl_code(base)
            if base in _CTRL_PUNCT:
                return bytes([_CTRL_PUNCT[base]])
            if len(base) == 1 and base.isdigit():  # ctrl+2/3/6/7 handled above, rest pass
                return None
            return None

        if key in ("enter", "return"):
            return self.enter_bytes()
        if key == "tab":
            return b"\t"
        if key == "escape":
            return b"\x1b"
        if key == "backspace":
            return self.backspace_bytes()

        if self.cfg.send_vt_sequences and base in _VT_SEQUENCES:
            return _VT_SEQUENCES[base]

        return None  # unmapped (shift combos, ctrl combos we don't know, ...)
