"""Unit tests for the pyte-backed terminal model."""

from __future__ import annotations

from pycom.termdisplay.vt import TerminalModel


def _text(rows) -> list[str]:
    return ["".join(c.data for c in row).rstrip() for row in rows]


def test_feed_basic_lines():
    m = TerminalModel(20, 6)
    m.feed_bytes(b"hello\r\nworld\r\n")
    lines = _text(m.screen_rows())
    assert lines[0] == "hello"
    assert lines[1] == "world"


def test_ansi_colors_parsed():
    m = TerminalModel(20, 6)
    m.feed_bytes(b"\x1b[31mred\x1b[0m ok")
    row = m.screen_rows()[0]
    chars = [c for c in row if c.data]
    assert any(c.fg == "red" for c in chars)


def test_carriage_return_overwrites():
    m = TerminalModel(20, 6)
    m.feed_bytes(b"12345\rABCD")
    line = _text(m.screen_rows())[0]
    assert line == "ABCD5"


def test_scroll_captures_history():
    m = TerminalModel(20, 3)  # tiny viewport -> scrolls quickly
    for i in range(6):
        m.feed_bytes(f"line{i}\r\n".encode())
    hist = _text(m.history_rows())
    scr = _text(m.screen_rows())
    assert hist, "history should not be empty"
    assert hist[0] == "line0"
    assert "line1" in hist
    # the newest visible lines are on screen
    assert "line5" in "".join(scr)


def test_rx_newline_conversion():
    m = TerminalModel(20, 6)
    m.rx_add_cr = True  # LF-only device output should also move to column 0
    m.feed_bytes(b"a\nb")
    lines = _text(m.screen_rows())
    assert lines[0] == "a"
    assert lines[1] == "b"


def test_clear():
    m = TerminalModel(20, 6)
    m.feed_bytes(b"some text")
    m.clear()
    assert not m.history_rows()
    assert not "".join(_text(m.screen_rows())).strip()
