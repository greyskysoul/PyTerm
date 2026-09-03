"""Unit tests for key -> bytes mapping."""

from __future__ import annotations

import pytest

from pyterm.config import AppConfig
from pyterm.keys import (
    KeyMapper,
    decode_escapes,
    format_hex,
    format_hex_lines,
    hex_bytes_per_line,
    parse_hex_line,
)


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


# --------------------------------------------------------------------------- bytes helpers


def test_decode_escapes():
    assert decode_escapes("AT\\r\\n") == b"AT\r\n"
    assert decode_escapes("\\x41\\x42") == b"AB"
    assert decode_escapes("a\\\\b") == b"a\\b"
    assert decode_escapes("\\t中") == b"\t" + "中".encode()
    assert decode_escapes("\\0end") == b"\x00end"
    assert decode_escapes("\\q") == b"\\q"  # unknown escape kept literally
    assert decode_escapes("trailing\\") == b"trailing\\"


def test_parse_hex_line():
    assert parse_hex_line("AA 0d, 7F") == b"\xaa\x0d\x7f"
    assert parse_hex_line("  1 2F ") == b"\x01\x2f"
    assert parse_hex_line("  ") == b""
    with pytest.raises(ValueError):
        parse_hex_line("GG")
    with pytest.raises(ValueError):
        parse_hex_line("123")


def test_format_hex():
    assert format_hex(b"\x01\xab\x0d") == "01 AB 0D"
    long = format_hex(bytes(range(18)))
    assert long.splitlines()[0].count(" ") == 15  # 16 bytes on the first line
    assert len(long.splitlines()) == 2


def test_hex_bytes_per_line():
    # 默认最大 16 字节/行（输入框使用）
    assert hex_bytes_per_line(200) == 16
    assert hex_bytes_per_line(47) == 16  # 16*3-1 = 47 刚好够
    assert hex_bytes_per_line(46) == 8
    assert hex_bytes_per_line(23) == 8
    assert hex_bytes_per_line(22) == 4
    assert hex_bytes_per_line(15) == 4  # 4*3-1 = 11 <= 15 < 23
    assert hex_bytes_per_line(5) == 4  # smallest supported line

    # HEX 接收显示允许更大的行：4/8/16/32
    assert hex_bytes_per_line(200, max_bytes=32) == 32
    assert hex_bytes_per_line(95, max_bytes=32) == 32  # 32*3-1 = 95 刚好够
    assert hex_bytes_per_line(94, max_bytes=32) == 16  # 差一列则退回 16
    assert hex_bytes_per_line(47, max_bytes=32) == 16
    assert hex_bytes_per_line(46, max_bytes=32) == 8
    assert hex_bytes_per_line(11, max_bytes=32) == 4  # 4*3-1 = 11
    assert hex_bytes_per_line(10, max_bytes=32) == 4  # 最小兜底


def test_format_hex_lines():
    assert format_hex_lines("AABBCCDDEEFF0102", 4) == "AA BB CC DD\nEE FF 01 02"
    assert format_hex_lines("AABBCC", 16) == "AA BB CC"
    assert format_hex_lines("ABC", 4) == "AB C"  # odd digit kept while typing
    assert format_hex_lines("", 4) == ""
