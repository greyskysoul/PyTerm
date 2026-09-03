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


# Cursor visuals ----------------------------------------------------------------
# The terminal shows an always-on cursor: a *filled block* while it is the
# active typing target, and a *vertical bar* once focus moves elsewhere.
_CURSOR_BLOCK_BG = Color.parse("#dde4ee")  # filled block background (active)
_CURSOR_BLOCK_FG = Color.parse("#0b0c12")  # glyph carved inside the block
_CURSOR_INACTIVE_FG = Color.parse("#9aa7b8")  # bar colour (inactive)
_BAR_CURSOR = "\u2502"  # vertical bar shown when the view is inactive


def _cursor_cell(under: str, active: bool) -> Text:
    """Build the single cell shown at the cursor position."""
    if active:
        return Text(
            (under or " "),
            style=Style(color=_CURSOR_BLOCK_FG, bgcolor=_CURSOR_BLOCK_BG),
        )
    return Text(_BAR_CURSOR, style=Style(color=_CURSOR_INACTIVE_FG))


def _overlay_at(row: Text, col: int, cell: Text) -> Text:
    """Place ``cell`` over character column ``col`` of ``row``."""
    if col < row.cell_len:
        return Text.assemble(row[:col], cell, row[col + 1 :])
    return Text.assemble(row, Text(" " * (col - row.cell_len)), cell)


class TerminalView(Static):
    """Scrollable terminal region driven by a :class:`TerminalModel`."""

    def __init__(self, model: TerminalModel, id: str | None = None) -> None:
        super().__init__("", id=id)
        self.model = model
        self._offset = 0  # rows scrolled back from the bottom
        self._auto_scroll = True
        self._active = True  # this view is the active typing target

    @property
    def active(self) -> bool:
        """True while this view is the active typing target (filled block
        cursor); False shows a hollow-box cursor instead."""
        return self._active

    @active.setter
    def active(self, value: bool) -> None:
        if value != self._active:
            self._active = value
            self.refresh()

    # -- lifecycle -------------------------------------------------------------------
    def on_mount(self) -> None:
        self._apply_size()
        self.refresh()

    def on_resize(self) -> None:
        self._apply_size()
        self.refresh()

    def _apply_size(self) -> None:
        w = max(1, self.size.width)
        h = max(1, self.size.height)
        if (w, h) != (self.model.columns, self.model.lines):
            self.model.resize(w, h)

    # -- external API ------------------------------------------------------------------
    def mark_dirty(self) -> None:
        """Ask Textual to repaint this view; :meth:`render` rebuilds the content
        straight from the model on the next paint.

        (Do NOT pump content through ``Static.update`` here: pushing a cached
        multi-line visual from a background timer races Textual's layout/paint
        cycle and the new content is frequently never composited.  A plain
        ``refresh()`` lets Textual call ``render()`` on its own schedule and is
        coalesced to at most one repaint per frame.)"""
        self.refresh()

    def scroll_to_bottom(self) -> None:
        self._auto_scroll = True
        self._offset = 0
        self.refresh()

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
        self.refresh()

    def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        event.stop()
        self._wheel(up=True)

    def on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        event.stop()
        self._wheel(up=False)

    # -- rendering -------------------------------------------------------------------------
    def render(self) -> Text:
        """Build the visible block from the model (called by Textual on repaint).

        An always-on cursor is drawn over the model's cursor cell: a filled
        block while the view is active, a vertical bar otherwise."""
        h = max(1, self.size.height)
        model = self.model
        if self._auto_scroll:
            self._offset = 0
        total = model.total_rows()
        history = model.history_rows()
        screen = model.screen_rows()
        cursor_row, cursor_col = model.cursor_position()

        if total <= h:
            pad = max(0, h - total)
            rows: list[Text] = [Text("")] * pad + [render_row(r) for r in screen]
            cursor_disp = pad + cursor_row
        else:
            end = total - self._offset
            start = max(0, end - h)
            all_rows = history + screen
            rows = [render_row(r) for r in all_rows[start:end]]
            if len(rows) < h:
                rows = [Text("")] * (h - len(rows)) + rows
            cursor_disp = len(history) + cursor_row - start

        if 0 <= cursor_disp < len(rows):
            row_chars = screen[cursor_row] if cursor_row < len(screen) else []
            # pyte leaves blank lines out of its buffer, so a row may be empty
            under = row_chars[cursor_col].data if cursor_col < len(row_chars) else " "
            rows[cursor_disp] = _overlay_at(
                rows[cursor_disp], cursor_col, _cursor_cell(under, self._active)
            )

        block = Text()
        for i, line in enumerate(rows):
            if i:
                block.append("\n")
            block.append_text(line)
        return block


class StatusBar(Static):
    """Bottom status line (port, counters, hints), updated by the app."""
