"""Small reusable modal building blocks."""

from __future__ import annotations

import contextlib
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    DirectoryTree,
    Input,
    Label,
    Select,
    Static,
)


class FieldSelect(Select, inherit_bindings=False):
    """表单下拉框（本应用所有模态共用）。

    本应用约定用方向键在对话框各字段之间移动焦点，因此折叠状态下方向键
    必须留给“移动焦点”，只有 Enter / Space 才打开下拉菜单。为此不继承
    父类把 ↑/↓ 也绑定到“打开菜单”的默认行为。
    """

    BINDINGS = [Binding("enter,space", "show_overlay", "打开", show=False)]


def _arrow_targets(screen: Screen) -> list[Widget]:
    """Focusable fields of a dialog, sorted by position (row-major)."""
    targets: list[Widget] = [
        w
        for w in screen.walk_children()
        if isinstance(w, (Button, Checkbox, Input, Select))
        and w.display
        and not w.disabled
        and w.can_focus
    ]
    targets.sort(key=lambda w: (w.region.y, w.region.x))
    return targets


def _handle_arrow_key(screen: Screen, event: Key) -> bool:
    """Arrow-key navigation shared by every modal.

    up/down/left/right move the focus (the "cursor") between the fields/buttons
    of the dialog, so the arrows work even when no text field is focused.  When
    the focused widget owns the arrows itself (Input caret editing, DataTable /
    DirectoryTree rows) the key is left alone to keep its native behaviour.
    Returns True when the key was consumed.
    """
    if event.key not in ("up", "down", "left", "right"):
        return False
    focused = screen.focused
    if isinstance(focused, (DataTable, DirectoryTree)):
        return False  # these widgets manage their own cursor
    if isinstance(focused, Input) and event.key in ("left", "right"):
        return False  # left/right keep editing the text caret
    targets = _arrow_targets(screen)
    if not targets or focused is None or focused not in targets:
        return False
    key = event.key
    if isinstance(focused, Checkbox) and key in ("left", "right"):
        focused.toggle()
        return True
    index = targets.index(focused)
    if key == "down":
        targets[(index + 1) % len(targets)].focus()
        return True
    if key == "up":
        targets[(index - 1) % len(targets)].focus()
        return True
    # left / right: move between fields that share the same row
    row = [w for w in targets if w.region.y == focused.region.y]
    if len(row) <= 1:
        return False
    rindex = row.index(focused)
    delta = -1 if key == "left" else 1
    row[(rindex + delta) % len(row)].focus()
    return True


class ModalBase(Screen):
    """Screen with a border/title that closes on ``escape`` (returns ``None``)."""

    BINDINGS = [("escape", "close", "关闭")]

    def action_close(self) -> None:
        self.dismiss(None)

    def on_key(self, event: Key) -> None:
        if _handle_arrow_key(self, event):
            event.stop()


class AdaptiveModal(ModalBase):
    """Modal that auto-swaps between a rich boxed form and a compact simple
    layout when the terminal is too small (see MIN_WIDTH / MIN_HEIGHT).

    Only one variant is ever mounted, so both variants may reuse the same
    widget ids; the code below keeps the user's in-progress edits when the
    window is resized across the threshold.

    Subclasses must provide:
      * ROOT_ID        — id shared by the single root container of both layouts
      * MIN_WIDTH/H    — below these the compact layout is used
      * build_rich()   — return the root widget of the full layout
      * build_compact()— return the root widget of the simple layout
      * after_build()  — populate values & set focus after (re)mounting
    """

    ROOT_ID: ClassVar[str] = ""
    MIN_WIDTH: ClassVar[int] = 0
    MIN_HEIGHT: ClassVar[int] = 0

    _compact: bool = False
    _pending_swap: bool = False
    _swap_busy: bool = False

    def _wants_compact(self) -> bool:
        size = self.size
        return size.width < self.MIN_WIDTH or size.height < self.MIN_HEIGHT

    # -- layout -------------------------------------------------------------
    def compose(self) -> ComposeResult:
        self._compact = self._wants_compact()
        yield self._root_for_mode()

    def _root_for_mode(self) -> Widget:
        root = self.build_compact() if self._compact else self.build_rich()
        if self._compact:
            root.add_class("compact")
        return root

    def build_rich(self) -> Widget:
        raise NotImplementedError

    def build_compact(self) -> Widget:
        raise NotImplementedError

    def after_build(self) -> None:
        """Hook called whenever the current variant finished mounting."""
        raise NotImplementedError

    # -- resize handling ----------------------------------------------------
    def on_mount(self) -> None:
        self._pending_swap = False
        self._swap_busy = False
        # compose() guessed the mode from the then-current size; a resize may
        # have happened before we actually mounted, so re-check once.
        if self._wants_compact() != self._compact:
            self._schedule_swap()
        else:
            self.after_build()

    def on_resize(self, _event) -> None:
        if self._wants_compact() != self._compact:
            self._schedule_swap()

    def _schedule_swap(self) -> None:
        """Rebuild the current variant to match the terminal size.  Resize
        events that arrive while a rebuild is in flight only set a flag, so
        rapid resizing collapses into a single extra pass (no cancelled
        workers / no duplicated rebuilds)."""
        self._pending_swap = True
        if self._swap_busy:
            return
        self._swap_busy = True
        self.run_worker(
            self._swap_loop(),
            name="modal-layout",
            group="modal-layout",
            exit_on_error=False,
        )

    async def _swap_loop(self) -> None:
        try:
            while self._pending_swap:
                self._pending_swap = False
                await self._do_swap()
        finally:
            self._swap_busy = False

    async def _do_swap(self) -> None:
        if not self.is_attached:
            return
        wanted = self._wants_compact()
        if wanted == self._compact:
            return
        captured = self._capture_values()
        old = self.query_one(f"#{self.ROOT_ID}")
        await old.remove()
        self._compact = wanted
        await self.mount(self._root_for_mode())
        self.after_build()
        self._restore_values(captured)

    # -- keep in-progress edits across the swap ------------------------------
    def _capture_values(self) -> dict[str, object]:
        captured: dict[str, object] = {}
        for widget in self.query(f"#{self.ROOT_ID} *"):
            if widget.id and isinstance(widget, (Checkbox, Input, Select)):
                captured[widget.id] = widget.value
        return captured

    def _restore_values(self, captured: dict[str, object]) -> None:
        if not captured:
            return
        for widget in self.query(f"#{self.ROOT_ID} *"):
            if not widget.id or widget.id not in captured:
                continue
            value = captured[widget.id]
            if isinstance(widget, Checkbox):
                widget.value = bool(value)
            elif isinstance(widget, Input):
                widget.value = str(value)
            elif isinstance(widget, Select):
                with contextlib.suppress(Exception):
                    widget.value = value  # type: ignore[assignment]


class ResponsiveCompact(ModalBase):
    """Modal that switches its root to a full-screen ``compact`` layout when
    the terminal is too small for the normal boxed one.

    Unlike AdaptiveModal both variants share the exact same widget tree — only
    the CSS differs — so resizing just toggles a ``compact`` class on the root:
    no DOM rebuild, no in-progress state to preserve.  Subclasses set ROOT_ID
    plus the MIN_WIDTH / MIN_HEIGHT thresholds below which ``compact`` applies.

    Subclasses that define their own ``on_mount`` must call ``super().on_mount()``.
    """

    ROOT_ID: ClassVar[str] = ""
    MIN_WIDTH: ClassVar[int] = 0
    MIN_HEIGHT: ClassVar[int] = 0

    def _wants_compact(self) -> bool:
        size = self.size
        return size.width < self.MIN_WIDTH or size.height < self.MIN_HEIGHT

    def _apply_compact(self) -> None:
        with contextlib.suppress(Exception):  # DOM not ready / already torn down
            self.query_one(f"#{self.ROOT_ID}").set_class(self._wants_compact(), "compact")

    def on_mount(self) -> None:
        self._apply_compact()

    def on_resize(self, _event=None) -> None:
        self._apply_compact()


class ConfirmDialog(ResponsiveCompact):
    """Yes/No confirmation modal; dismisses with ``True`` / ``False``.

    On terminals narrower/shorter than MIN_WIDTH/MIN_HEIGHT the root box gets
    the ``compact`` class (full width, no margins) so it never overflows.
    """

    ROOT_ID = "confirm"
    # 富布局盒子宽 54 且带 2*4 外边距，需要约 62 列；更小则切换为整宽布局
    MIN_WIDTH = 62
    MIN_HEIGHT = 14

    BINDINGS = [("escape", "no", "否")]

    def __init__(
        self,
        title: str,
        message: str,
        yes: str = "是",
        no: str = "否",
    ) -> None:
        super().__init__()
        self._title = title
        self._message = message
        self._yes = yes
        self._no = no

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm"):
            yield Static(self._title, id="confirm-title")
            yield Label(self._message, id="confirm-message")
            with Horizontal(id="confirm-buttons"):
                yield Button(self._yes, variant="primary", id="yes", compact=True)
                yield Button(self._no, variant="default", id="no", compact=True)

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one("#yes", Button).focus()

    def _click(self, result: bool) -> None:
        self.dismiss(result)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self._click(event.button.id == "yes")

    def action_no(self) -> None:
        self.dismiss(False)
