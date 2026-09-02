"""Options / preferences dialog."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Checkbox, Input, Label, Static

from pyterm.config import save_config
from pyterm.screens.base import ModalBase


class OptionsScreen(ModalBase):
    """Edit persisted options; on save they are applied immediately."""

    def compose(self) -> ComposeResult:
        with Vertical(id="options-box"):
            yield Static("选项设置", id="options-title")
            with VerticalScroll(id="options-body"):
                yield Checkbox("本地回显（E）", id="echo")
                yield Checkbox("自动回绕（A）", id="wrap")
                yield Checkbox("接收 LF → CR+LF", id="rx_cr")
                yield Checkbox("接收 CR → CR+LF", id="rx_lf")
                yield Checkbox("捕获时加时间戳", id="ts")
                yield Checkbox("发送方向键/功能键 VT 序列", id="vt")
                with Horizontal(classes="form-row"):
                    yield Label("回车发送", classes="form-label")
                    yield Input("", id="enter", placeholder="cr / crlf / lf / none")
                    yield Label("退格发送", classes="form-label")
                    yield Input("", id="back", placeholder="del / bs")
                with Horizontal(classes="form-row"):
                    yield Label("解码字符集", classes="form-label")
                    yield Input("", id="decode", placeholder="utf-8 / gbk / latin-1")
                    yield Label("传输超时(s)", classes="form-label")
                    yield Input("", id="timeout", placeholder="10")
                with Horizontal(classes="form-row"):
                    yield Label("重试次数", classes="form-label")
                    yield Input("", id="retries", placeholder="10")
                    yield Label("数据块", classes="form-label")
                    yield Input("", id="blocksize", placeholder="1024 / 128")
            with Horizontal(id="options-buttons"):
                yield Button("保存", id="save", variant="primary")
                yield Button("取消", id="cancel")

    def on_mount(self) -> None:
        cfg = self.app.cfg  # type: ignore[attr-defined]
        self.query_one("#echo", Checkbox).value = cfg.local_echo
        self.query_one("#wrap", Checkbox).value = cfg.wrap
        self.query_one("#rx_cr", Checkbox).value = cfg.rx_add_cr
        self.query_one("#rx_lf", Checkbox).value = cfg.rx_add_lf
        self.query_one("#ts", Checkbox).value = cfg.capture_timestamps
        self.query_one("#vt", Checkbox).value = cfg.send_vt_sequences
        self.query_one("#enter", Input).value = cfg.enter_sends
        self.query_one("#back", Input).value = cfg.backspace_sends
        self.query_one("#decode", Input).value = cfg.decode
        self.query_one("#timeout", Input).value = str(cfg.xfer_timeout)
        self.query_one("#retries", Input).value = str(cfg.xfer_retries)
        self.query_one("#blocksize", Input).value = str(cfg.xfer_block_size)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._save()
        else:
            self.dismiss(None)

    def _save(self) -> None:
        cfg = self.app.cfg  # type: ignore[attr-defined]
        cfg.local_echo = self.query_one("#echo", Checkbox).value
        cfg.wrap = self.query_one("#wrap", Checkbox).value
        cfg.rx_add_cr = self.query_one("#rx_cr", Checkbox).value
        cfg.rx_add_lf = self.query_one("#rx_lf", Checkbox).value
        cfg.capture_timestamps = self.query_one("#ts", Checkbox).value
        cfg.send_vt_sequences = self.query_one("#vt", Checkbox).value
        cfg.enter_sends = self.query_one("#enter", Input).value.strip() or "cr"
        cfg.backspace_sends = self.query_one("#back", Input).value.strip() or "del"
        cfg.decode = self.query_one("#decode", Input).value.strip() or "utf-8"
        try:
            cfg.xfer_timeout = float(self.query_one("#timeout", Input).value)
            cfg.xfer_retries = int(self.query_one("#retries", Input).value)
            cfg.xfer_block_size = int(self.query_one("#blocksize", Input).value)
        except ValueError:
            pass
        if cfg.enter_sends not in ("cr", "crlf", "lf", "none"):
            cfg.enter_sends = "cr"
        if cfg.backspace_sends not in ("del", "bs"):
            cfg.backspace_sends = "del"
        if cfg.decode not in ("utf-8", "gbk", "latin-1"):
            cfg.decode = "utf-8"
        if cfg.xfer_block_size not in (128, 1024):
            cfg.xfer_block_size = 1024
        save_config(cfg)
        self.app.apply_config()  # type: ignore[attr-defined]
        self.dismiss(None)
