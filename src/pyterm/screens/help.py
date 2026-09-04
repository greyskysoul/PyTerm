"""The Ctrl+A Z main menu / help overlay (minicom-style)."""

from __future__ import annotations

from functools import partial

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.events import Key
from textual.widgets import Button, Label, Static

from pyterm import PROJECT_AUTHOR, PROJECT_URL, __version__
from pyterm.screens.base import ResponsiveCompact

# 菜单文案：只保留简洁功能名，去掉冗余的括号说明（YMODEM 用于标明传输协议）
MENU_ITEMS = [
    ("z", "主菜单 / 帮助"),
    ("s", "发送文件 (YMODEM)"),
    ("r", "接收文件 (YMODEM)"),
    ("c", "清屏"),
    ("l", "会话捕获 开 / 关"),
    ("h", "16 进制接收/发送 开 / 关"),
    ("p", "串口参数"),
    ("o", "选项设置"),
    ("x", "退出"),
]


def _menu_body() -> ComposeResult:
    """The menu buttons shared by both (rich / compact) layouts."""
    for key, desc in MENU_ITEMS:
        yield Button(
            f"  {key}   {desc}",
            id=f"menu-{key}",
            classes="menu-item",
            compact=True,
        )


class MainMenuScreen(ResponsiveCompact):
    """Overlay listing the Ctrl+A functions; a single letter key runs one.

    Small terminals toggle the ``compact`` class on ``#help-box`` (see
    ResponsiveCompact), turning the boxed list into a full-screen scrollable
    one so every item stays reachable.
    """

    ROOT_ID = "help-box"
    # 富布局盒子含底部 about 两行与左下“返回”按钮，内容更高；高度 <30 就
    # 整屏简洁布局，保证小窗口下所有条目与返回按钮都完整可见可用。
    MIN_WIDTH = 50
    MIN_HEIGHT = 30

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Static("PyTerm - Ctrl+A 功能菜单", id="help-title")
            with VerticalScroll(id="help-body"):
                yield from _menu_body()
            yield Label("方向键选择；Enter 或功能字母执行；Esc 关闭", id="help-footer")
            yield Label(f"PyTerm v{__version__} · {PROJECT_AUTHOR}", id="help-about")
            yield Label(PROJECT_URL, id="help-repo")
            yield Button("返回", id="menu-back", compact=True)

    def on_mount(self) -> None:
        super().on_mount()
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
        if button_id == "menu-back":
            event.stop()
            self.dismiss(None)  # 返回主界面
            return
        if not button_id.startswith("menu-"):
            return
        code = button_id[len("menu-") :]
        codes = {key for key, _ in MENU_ITEMS}
        if code in codes:
            event.stop()
            self._run(code)
