"""YMODEM send / receive screens (progress, cancel)."""

from __future__ import annotations

import os
import re
import time
from typing import ClassVar

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.widgets import Button, Input, Label, Static

from pycom.screens.base import ModalBase
from pycom.screens.filepicker import PathPicker

_SAFE_NAME = re.compile(r"[^\w.\- ]")


def sanitize_filename(name: str) -> str:
    name = os.path.basename(name.replace("\\", "/")).strip()
    name = _SAFE_NAME.sub("_", name)
    return name or "download.bin"


class _TransferScreen(ModalBase):
    """Shared behaviour for the send & receive dialogs."""

    BINDINGS: ClassVar[list] = []

    active: bool = False
    _last_phase: str = ""
    _last_update: float = 0.0

    def _make_progress_area(self) -> Static:
        return Static("就绪。", id="xfer-state")

    def _update_state(self, text: str) -> None:
        self.query_one("#xfer-state", Static).update(text)

    def _on_start_clicked(self) -> None:  # overridden
        raise NotImplementedError

    def _browse(self) -> None:
        """Open a path picker — overridden by subclasses."""

    def _start(self, ok: bool) -> None:
        self.active = ok
        self.query_one("#start", Button).disabled = ok
        self.query_one("#cancel", Button).disabled = not ok
        self._last_update = 0.0

    def on_key(self, event: Key) -> None:
        if event.key in ("up", "down", "left", "right"):
            super().on_key(event)  # shared arrow-key navigation
            return
        if event.key == "escape":
            event.stop()
            if self.active:
                # Esc while transferring = request to cancel (dialog stays open)
                self.app.cancel_transfer()  # type: ignore[attr-defined]
                self._update_state("正在取消…")
            else:
                self.dismiss(None)

    # -- called from the transfer worker (via call_from_thread) ------------------------
    def show_progress(
        self,
        phase: str,
        filename: str,
        sent: int,
        total: int | None,
    ) -> None:
        now = time.monotonic()
        if phase and phase != self._last_phase:
            self._last_phase = phase
        elif now - self._last_update < 0.1:
            return
        self._last_update = now
        pct = f"{sent * 100 // total}%" if total else ""
        self._update_state(
            f"{phase}  {filename}  {sent:,} / {total:,} B {pct}"
            if total
            else f"{phase}  {filename}  {sent:,} B"
        )

    def show_result(self, ok: bool, reason: str) -> None:
        self._start(False)
        self._update_state(f"{'完成' if ok else '失败/中止'}: {reason}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "start":
            self._on_start_clicked()
        elif bid == "cancel":
            self.app.cancel_transfer()  # type: ignore[attr-defined]
            self._update_state("正在取消…")
        elif bid == "browse":
            self._browse()
        elif bid == "close" and not self.active:
            self.dismiss(None)


class SendScreen(_TransferScreen):
    """Send a file to the device with YMODEM."""

    def compose(self) -> ComposeResult:
        with Vertical(id="xfer-box"):
            yield Static("发送文件 - YMODEM", id="xfer-title")
            with Horizontal(classes="form-row"):
                yield Label("文件", classes="form-label")
                yield Input("", id="file", placeholder="要发送的文件路径")
                yield Button("浏览…", id="browse", compact=True)
            with Horizontal(id="xfer-buttons"):
                yield Button("开始发送", id="start", variant="primary", compact=True)
                yield Button("取消传输", id="cancel", disabled=True, compact=True)
                yield Button("关闭", id="close", compact=True)
            yield self._make_progress_area()

    def on_mount(self) -> None:
        cfg = self.app.cfg  # type: ignore[attr-defined]
        self.query_one("#file", Input).value = getattr(cfg, "_last_send_file", "") or os.getcwd()
        self._last_update = 0.0
        self._last_phase = ""
        self.query_one("#file", Input).focus()

    def _browse(self) -> None:
        def _done(path: str | None) -> None:
            if path:
                self.query_one("#file", Input).value = path

        cur = self.query_one("#file", Input).value or os.getcwd()
        self.app.push_screen(PathPicker(cur, pick_files=True), _done)  # type: ignore[attr-defined]

    def _on_start_clicked(self) -> None:
        path = self.query_one("#file", Input).value.strip()
        if not path or not os.path.isfile(path):
            self._update_state("文件不存在: " + path)
            return
        err = self.app.start_transfer_send(path)  # type: ignore[attr-defined]
        if err:
            self._update_state(err)
            return
        self._start(True)
        self._update_state(
            f"发送 {os.path.basename(path)} — 等待设备进入接收状态 (Ctrl+A S 前请先在对端启动接收)…"
        )


class RecvScreen(_TransferScreen):
    """Receive a file from the device with YMODEM."""

    def compose(self) -> ComposeResult:
        with Vertical(id="xfer-box"):
            yield Static("接收文件 - YMODEM", id="xfer-title")
            with Horizontal(classes="form-row"):
                yield Label("保存目录", classes="form-label")
                yield Input("", id="dir", placeholder="目录")
                yield Button("浏览…", id="browse", compact=True)
            with Horizontal(classes="form-row"):
                yield Label("文件名", classes="form-label")
                yield Input("", id="name", placeholder="留空 = 使用设备发送的文件名")
            with Horizontal(id="xfer-buttons"):
                yield Button("开始接收", id="start", variant="primary", compact=True)
                yield Button("取消传输", id="cancel", disabled=True, compact=True)
                yield Button("关闭", id="close", compact=True)
            yield self._make_progress_area()

    def on_mount(self) -> None:
        self.query_one("#dir", Input).value = os.getcwd()
        self._last_update = 0.0
        self._last_phase = ""
        self.query_one("#dir", Input).focus()

    def _browse(self) -> None:
        def _done(path: str | None) -> None:
            if path:
                self.query_one("#dir", Input).value = path

        cur = self.query_one("#dir", Input).value or os.getcwd()
        self.app.push_screen(PathPicker(cur, pick_files=False), _done)  # type: ignore[attr-defined]

    def _on_start_clicked(self) -> None:
        directory = self.query_one("#dir", Input).value.strip() or os.getcwd()
        if not os.path.isdir(directory):
            self._update_state("目录不存在: " + directory)
            return
        name = self.query_one("#name", Input).value.strip()
        err = self.app.start_transfer_recv(directory, name)  # type: ignore[attr-defined]
        if err:
            self._update_state(err)
            return
        self._start(True)
        self._update_state("等待设备发送 (请先在对端启动 YMODEM 发送)…")
