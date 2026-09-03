"""The Ctrl+A Z main menu / help overlay (minicom-style)."""

from __future__ import annotations

from functools import partial

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.events import Key
from textual.widgets import Button, Label, Static

from pyterm.screens.base import ModalBase

MENU_ITEMS = [
    ("z", "主菜单 / 帮助（本窗口）"),
    ("s", "发送文件 (YMODEM)"),
    ("r", "接收文件 (YMODEM)"),
    ("c", "清屏"),
    ("l", "会话捕获 (log) 开 / 关"),
    ("h", "16 进制接收/发送（HEX）开 / 关"),
    ("p", "串口参数（连接设置）"),
    ("o", "选项设置（本地回显 / 自动回绕等）"),
    ("x", "退出 PyTerm"),
]


class MainMenuScreen(ModalBase):
    """Overlay listing the Ctrl+A functions; a single letter key runs one."""

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Static("PyTerm - Ctrl+A 功能菜单", id="help-title")
            with VerticalScroll(id="help-body"):
                for key, desc in MENU_ITEMS:
                    yield Button(
                        f"  {key}   {desc}",
                        id=f"menu-{key}",
                        classes="menu-item",
                        compact=True,
                    )
            yield Label("方向键选择；Enter 或功能字母执行；Esc 关闭", id="help-footer")

    def on_mount(self) -> None:
        self.query_one(".menu-item", Button).focus()

    def _run(self, code: str) -> None:
        self.dismiss(None)
        # Run the action after this modal is gone.  NOTE: do not use a 0.0
        # timer delay — Textual 8's Timer divides by the delay and crashes
        # with ZeroDivisionError, so the menu action would never run.
        self.app.set_timer(0.05, partial(self.app.menu_action, code))  # type: ignore[attr-defined]

    def on_key(self, event: Key) -> None:
        if event.key == "escape":
            return  # handled by binding
        if event.key in ("up", "down", "left", "right"):
            super().on_key(event)  # shared arrow-key navigation
            return
        char = (event.character or "").lower()
        if not char:
            return
        code = char[0]
        codes = {key for key, _ in MENU_ITEMS}
        if code in codes:
            event.stop()
            self._run(code)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith("menu-"):
            return
        code = button_id[len("menu-") :]
        codes = {key for key, _ in MENU_ITEMS}
        if code in codes:
            event.stop()
            self._run(code)
