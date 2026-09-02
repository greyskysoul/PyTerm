"""Small reusable modal building blocks."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Label, Static


class ModalBase(Screen):
    """Screen with a border/title that closes on ``escape`` (returns ``None``)."""

    BINDINGS = [("escape", "close", "关闭")]

    def action_close(self) -> None:
        self.dismiss(None)


class ConfirmDialog(Screen[bool]):
    """Yes/No confirmation modal; dismisses with ``True`` / ``False``."""

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
                yield Button(self._yes, variant="primary", id="yes")
                yield Button(self._no, variant="default", id="no")

    def on_mount(self) -> None:
        self.query_one("#yes", Button).focus()

    def _click(self, result: bool) -> None:
        self.dismiss(result)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self._click(event.button.id == "yes")

    def action_no(self) -> None:
        self.dismiss(False)
