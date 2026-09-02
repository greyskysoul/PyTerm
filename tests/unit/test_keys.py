"""Unit tests for key -> bytes mapping."""

from __future__ import annotations

from pyterm.config import AppConfig
from pyterm.keys import KeyMapper


def _mapper(**overrides) -> KeyMapper:
    cfg = AppConfig()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return KeyMapper(cfg)


def test_printable_ascii():
    assert _mapper().map("a", "a") == b"a"
    assert _mapper().map("shift+a", "A") == b"A"


def test_printable_unicode():
    assert _mapper().map("?", "中") == "中".encode()


def test_ctrl_letters():
    m = _mapper()
    assert m.map("ctrl+a", None) == b"\x01"
    assert m.map("ctrl+z", None) == b"\x1a"
    assert m.map("ctrl+m", None) == b"\r"


def test_enter_modes():
    assert _mapper().map("enter", None) == b"\r"
    assert _mapper(enter_sends="crlf").map("enter", None) == b"\r\n"
    assert _mapper(enter_sends="lf").map("enter", None) == b"\n"
    assert _mapper(enter_sends="none").map("enter", None) == b""


def test_backspace_modes():
    assert _mapper().map("backspace", None) == b"\x7f"
    assert _mapper(backspace_sends="bs").map("backspace", None) == b"\x08"


def test_special_keys():
    m = _mapper()
    assert m.map("tab", None) == b"\t"
    assert m.map("escape", None) == b"\x1b"


def test_vt_sequences_toggle():
    assert _mapper().map("up", None) == b"\x1b[A"
    assert _mapper().map("f5", None) == b"\x1b[15~"
    assert _mapper(send_vt_sequences=False).map("up", None) is None


def test_unknown_key_not_sent():
    assert _mapper().map("ctrl+shift+1", None) is None
