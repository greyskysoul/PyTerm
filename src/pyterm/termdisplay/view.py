"""Textual widgets for the terminal area and the minicom-style status bar."""

from __future__ import annotations

import re
from functools import lru_cache

from rich.color import Color
from rich.style import Style
from rich.text import Text
from textual.events import MouseScrollDown, MouseScrollUp
from textual.widgets import Static

from pyterm.termdisplay.vt import Char, TerminalModel

_HEX_RE = re.compile(r"^[0-9a-fA-F]{6}$")
_ANSI_BASE = {
    "black": 0,
    "red": 1,
    "green": 2,
    "brown": 3,
    "yellow": 3,
    "blue": 4,
    "magenta": 5,
    "cyan": 6,
    "white": 7,
}


def _resolve_color(name: str) -> Color | None:
    if name == "default":
        return None
    if name.startswith("bright"):
        base = _ANSI_BASE.get(name[6:])
        return Color.from_ansi(base + 8) if base is not None else None
    base = _ANSI_BASE.get(name)
    if base is not None:
        return Color.from_ansi(base)
    if _HEX_RE.match(name):
        try:
            return Color.parse("#" + name)
        except Exception:
            return None
    return None


@lru_cache(maxsize=1024)
def _char_style(
    fg: str, bg: str, bold: bool, italics: bool, underline: bool, reverse: bool
) -> Style:
    fg_c = _resolve_color(fg)
    bg_c = _resolve_color(bg)
    if reverse:
        fg_c, bg_c = bg_c, fg_c
    return Style(
        color=fg_c,
        bgcolor=bg_c,
        bold=bold,
        italic=italics,
        underline=underline,
    )


def render_row(chars: list[Char]) -> Text:
    """Render one model row (list of pyte Chars) into a rich :class:`Text`."""
    if not chars:
        return Text("")

    # trim trailing default cells (spaces with no style)
    end = len(chars)
    while end > 0:
        c = chars[end - 1]
        if (
            c.data in (" ", "")
            and c.fg == "default"
            and c.bg == "default"
            and not (c.bold or c.italics or c.underscore or c.reverse)
        ):
            end -= 1
        else:
            break

    text = Text()
    run: list[str] = []
    run_style: Style | None = None

    def flush() -> None:
        nonlocal run, run_style
        if run:
            text.append("".join(run), style=run_style)
            run = []

    for i in range(end):
        c = chars[i]
        style = _char_style(c.fg, c.bg, c.bold, c.italics, c.underscore, c.reverse)
        if run and style == run_style:
            run.append(c.data)
        else:
            flush()
            run_style = style
            run.append(c.data)
    flush()
    return text


class TerminalView(Static):
    """Scrollable terminal region driven by a :class:`TerminalModel`."""

    def __init__(self, model: TerminalModel, id: str | None = None) -> None:
        super().__init__("", id=id)
        self.model = model
        self._offset = 0  # rows scrolled back from the bottom
        self._auto_scroll = True
        self._dirty = True

    # -- lifecycle -------------------------------------------------------------------
    def on_mount(self) -> None:
        self.set_interval(1 / 20.0, self._maybe_redraw)
        self._apply_size()

    def on_resize(self) -> None:
        self._apply_size()
        self.mark_dirty()

    def _apply_size(self) -> None:
        w = max(1, self.size.width)
        h = max(1, self.size.height)
        if (w, h) != (self.model.columns, self.model.lines):
            self.model.resize(w, h)

    # -- external API ------------------------------------------------------------------
    def mark_dirty(self) -> None:
        self._dirty = True

    def scroll_to_bottom(self) -> None:
        self._auto_scroll = True
        self._offset = 0
        self.mark_dirty()

    # -- input ---------------------------------------------------------------------------
    def _wheel(self, up: bool) -> None:
        total = self.model.total_rows()
        if total <= self.model.lines:
            return
        if up:
            self._auto_scroll = False
            self._offset = min(self._offset + 3, max(0, total - self.model.lines))
        else:
            self._offset = max(0, self._offset - 3)
            if self._offset == 0:
                self._auto_scroll = True
        self.mark_dirty()

    def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        event.stop()
        self._wheel(up=True)

    def on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        event.stop()
        self._wheel(up=False)

    # -- rendering -------------------------------------------------------------------------
    def _maybe_redraw(self) -> None:
        if self._dirty:
            self._dirty = False
            self._render_content()

    def _render_content(self) -> None:
        h = max(1, self.size.height)
        model = self.model
        if self._auto_scroll:
            self._offset = 0
        total = model.total_rows()

        if total <= h:
            content_rows = [render_row(r) for r in model.screen_rows()]
            rows: list[Text] = [Text("")] * (h - total) + content_rows
        else:
            end = total - self._offset
            start = max(0, end - h)
            all_rows = model.history_rows() + model.screen_rows()
            rows = [render_row(r) for r in all_rows[start:end]]
            rows = [Text("")] * (h - len(rows)) + rows

        block = Text()
        for i, line in enumerate(rows):
            if i:
                block.append("\n")
            block.append_text(line)
        self.update(block)


class StatusBar(Static):
    """Bottom status line (port, counters, hints), updated by the app."""
