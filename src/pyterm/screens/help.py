"""The Ctrl+A Z main menu / help overlay (minicom-style)."""

from __future__ import annotations

from functools import partial

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.events import Key
from textual.widgets import Label, Static

from pyterm.screens.base import ModalBase

MENU_ITEMS = [
    ("z", "主菜单 / 帮助（本窗口）"),
    ("s", "发送文件 (YMODEM)"),
    ("r", "接收文件 (YMODEM)"),
    ("c", "清屏"),
    ("l", "会话捕获 (log) 开 / 关"),
    ("p", "串口参数（连接 / 断开）"),
    ("o", "选项设置"),
    ("a", "自动回绕 开 / 关"),
    ("e", "本地回显 开 / 关"),
    ("x", "退出 PyTerm"),
]


class MainMenuScreen(ModalBase):
    """Overlay listing the Ctrl+A functions; a single letter key runs one."""

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Static("PyTerm — Ctrl+A 功能菜单", id="help-title")
            with VerticalScroll(id="help-body"):
                yield Label("按下列按键执行对应功能（Ctrl+A Ctrl+A 向设备发送 0x01，Esc 关闭）：")
                for key, desc in MENU_ITEMS:
                    yield Label(f"   {key:<3}  {desc}", classes="help-item")
                yield Label("   ⎇  再次按 Ctrl+A  = 发送字节 0x01", classes="help-item")
            yield Label("按功能键 / Esc 关闭", id="help-footer")

    def on_key(self, event: Key) -> None:
        if event.key == "escape":
            return  # handled by binding
        char = (event.character or "").lower()
        if not char:
            return
        code = char[0]
        codes = {key for key, _ in MENU_ITEMS}
        if code in codes:
            event.stop()
            self.dismiss(None)
            self.app.set_timer(0.0, partial(self.app.menu_action, code))  # type: ignore[attr-defined]
