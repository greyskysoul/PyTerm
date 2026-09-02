"""Pure-data terminal emulation model built on :mod:`pyte`.

A :class:`TerminalModel` owns a pyte :class:`~pyte.screens.Screen` plus its own
scrollback history.  pyte only models the *visible* screen and discards rows
that scroll off the top; we subclass its ``Screen`` and override ``index()`` to
capture the about-to-be-lost top row into a ``deque`` scrollback.

Everything in this module is UI-agnostic: it never imports textual, so it can
be unit-tested and reused from non-TUI code.
"""

from __future__ import annotations

import codecs
from collections import deque
from collections.abc import Callable, Sequence

import pyte
from pyte.screens import Char, Margins

DEFAULT_SCROLLBACK = 4000
_DEFAULT_CODECS = ("utf-8", "gbk", "latin-1")


def _row_is_blank(chars: Sequence[Char]) -> bool:
    return not any(c.data not in (" ", "") for c in chars)


class _CaptureScreen(pyte.screens.Screen):
    """pyte Screen that reports rows scrolling off the top of a full-screen scroll."""

    def __init__(
        self,
        columns: int,
        lines: int,
        on_scroll_out: Callable[[list[Char]], None] | None,
    ) -> None:
        super().__init__(columns, lines)
        self._scroll_cb = on_scroll_out  # callable(list[Char]) | None

    def snapshot_row(self, y: int) -> list[Char]:
        row = self.buffer.get(y)
        if row is None:
            return []
        out: list[Char] = []
        for x in range(self.columns):
            out.append(row[x])
        return out

    def index(self) -> None:
        top, bottom = self.margins or Margins(0, self.lines - 1)
        if self.cursor.y == bottom and top == 0:
            # A true full-screen scroll: the top row is about to be lost.
            top_row = self.snapshot_row(0)
            if self._scroll_cb is not None and top_row and not _row_is_blank(top_row):
                self._scroll_cb(top_row)
        super().index()


class TerminalModel:
    """In-memory terminal: feed it bytes, read styled rows for display."""

    def __init__(
        self,
        columns: int = 80,
        lines: int = 24,
        scrollback: int = DEFAULT_SCROLLBACK,
        decode: str = "utf-8",
    ) -> None:
        self.columns = columns
        self.lines = lines
        self.decode = decode
        self.rx_add_cr = False
        self.rx_add_lf = False

        self._history: deque[list[Char]] = deque(maxlen=max(0, scrollback))
        self._decoder = self._make_decoder(decode)
        self._screen = _CaptureScreen(columns, lines, self._on_scroll_out)
        self._stream = pyte.Stream(self._screen)

    # -- construction helpers ---------------------------------------------------------
    def _make_decoder(self, codec: str):
        name = codec if codec in _DEFAULT_CODECS else "utf-8"
        return codecs.getincrementaldecoder(name)(errors="replace")

    def set_decode(self, codec: str) -> None:
        self.decode = codec if codec in _DEFAULT_CODECS else "utf-8"
        self._decoder = self._make_decoder(self.decode)

    # -- event hooks -------------------------------------------------------------------
    def _on_scroll_out(self, row: list[Char]) -> None:
        self._history.append(row)

    # -- sizing -------------------------------------------------------------------------
    def resize(self, columns: int, lines: int) -> None:
        """Recreate the screen at the new size (display is cleared, history kept)."""
        if columns < 1:
            columns = 1
        if lines < 1:
            lines = 1
        self.columns = columns
        self.lines = lines
        self._screen = _CaptureScreen(columns, lines, self._on_scroll_out)
        self._stream = pyte.Stream(self._screen)

    # -- input --------------------------------------------------------------------------
    def feed_bytes(self, data: bytes) -> None:
        if not data:
            return
        text = data.decode("latin-1") if self.decode == "latin-1" else self._decoder.decode(data)
        self.feed_text(text)

    def feed_text(self, text: str) -> None:
        if not text:
            return
        if self.rx_add_cr:
            text = text.replace("\n", "\r\n")
        if self.rx_add_lf:
            text = text.replace("\r\n", "\r").replace("\r", "\r\n")
        self._stream.feed(text)

    # -- output ---------------------------------------------------------------------------
    def history_rows(self) -> list[list[Char]]:
        return list(self._history)

    def screen_rows(self) -> list[list[Char]]:
        return [self._screen.snapshot_row(y) for y in range(self.lines)]

    def total_rows(self) -> int:
        return len(self._history) + self.lines

    def clear(self) -> None:
        self._history.clear()
        self._screen.reset()

    # -- text helpers -----------------------------------------------------------------------
    def plain_lines(self) -> list[str]:
        """Current visible content as plain text lines (used by capture/copy)."""
        return ["".join(c.data for c in row) for row in self.screen_rows()]
