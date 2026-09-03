"""Options / preferences dialog."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.widgets import Button, Checkbox, Input, Label, Static

from pyterm.config import save_config
from pyterm.screens.base import AdaptiveModal, FieldSelect


class _CircleCheckbox(Checkbox):
    """Checkbox whose marker is a hollow circle (off) / solid circle (on)."""

    @property
    def _button(self) -> Content:
        style = self.get_visual_style("toggle--button")
        glyph = "\u25cf" if self.value else "\u25cb"  # ● / ○
        return Content.assemble((glyph, style))


# 下拉可选项：(显示文本, 存储值)，顺序即下拉菜单中的展示顺序
_ENTER_OPTIONS: list[tuple[str, str]] = [
    ("CR (回车)", "cr"),
    ("CR+LF", "crlf"),
    ("LF (换行)", "lf"),
    ("不发送", "none"),
]
_BACK_OPTIONS: list[tuple[str, str]] = [
    ("DEL (0x7F)", "del"),
    ("BS (0x08)", "bs"),
]
_DECODE_OPTIONS: list[tuple[str, str]] = [
    ("UTF-8", "utf-8"),
    ("GBK", "gbk"),
    ("Latin-1", "latin-1"),
]
_ENTER_VALUES = {value for _, value in _ENTER_OPTIONS}
_BACK_VALUES = {value for _, value in _BACK_OPTIONS}
_DECODE_VALUES = {value for _, value in _DECODE_OPTIONS}


def _circle_fields():
    """The seven boolean option checkboxes shared by both layouts."""
    return [
        _CircleCheckbox("本地回显", id="echo", compact=True),
        _CircleCheckbox("自动回绕", id="wrap", compact=True),
        _CircleCheckbox("接收 LF -> CR+LF", id="rx_cr", compact=True),
        _CircleCheckbox("接收 CR -> CR+LF", id="rx_lf", compact=True),
        _CircleCheckbox("捕获时加时间戳", id="ts", compact=True),
        _CircleCheckbox("发送方向键/功能键 VT 序列", id="vt", compact=True),
        _CircleCheckbox("16 进制接收/发送（HEX）", id="hex", compact=True),
    ]


def _field_row(label: str, control) -> Horizontal:
    """One full-width labelled row used by the compact layout."""
    with Horizontal(classes="c-row"):
        yield Label(label, classes="c-label")
        yield control


class _OptionsRich(Vertical):
    """Full layout: dense multi-row form inside a centred box."""

    def compose(self) -> ComposeResult:
        yield Static("选项设置", id="options-title")
        with VerticalScroll(id="options-body"):
            for checkbox in _circle_fields():
                yield checkbox
            with Horizontal(classes="form-row"):
                yield Label("回车发送", classes="form-label")
                yield FieldSelect(_ENTER_OPTIONS, id="enter", allow_blank=False, compact=True)
                yield Label("退格发送", classes="form-label")
                yield FieldSelect(_BACK_OPTIONS, id="back", allow_blank=False, compact=True)
            with Horizontal(classes="form-row"):
                yield Label("解码字符集", classes="form-label")
                yield FieldSelect(_DECODE_OPTIONS, id="decode", allow_blank=False, compact=True)
                yield Label("传输超时(s)", classes="form-label")
                yield Input("", id="timeout", placeholder="10", compact=True)
            with Horizontal(classes="form-row"):
                yield Label("重试次数", classes="form-label")
                yield Input("", id="retries", placeholder="10", compact=True)
                yield Label("数据块", classes="form-label")
                yield Input("", id="blocksize", placeholder="1024 / 128", compact=True)
        with Horizontal(id="options-buttons"):
            yield Button("保存", id="save", variant="primary", compact=True)
            yield Button("取消", id="cancel", compact=True)


class _OptionsCompact(Vertical):
    """Simple layout for small windows: each control on its own full-width
    row, inside a scroll area so very short windows stay usable."""

    def compose(self) -> ComposeResult:
        yield Static("选项设置 - 简洁模式", id="options-title")
        with VerticalScroll(id="options-body"):
            for checkbox in _circle_fields():
                yield checkbox
            yield from _field_row(
                "回车发送", FieldSelect(_ENTER_OPTIONS, id="enter", allow_blank=False, compact=True)
            )
            yield from _field_row(
                "退格发送", FieldSelect(_BACK_OPTIONS, id="back", allow_blank=False, compact=True)
            )
            yield from _field_row(
                "解码字符集", FieldSelect(_DECODE_OPTIONS, id="decode", allow_blank=False, compact=True)
            )
            yield from _field_row("传输超时(s)", Input("", id="timeout", placeholder="10", compact=True))
            yield from _field_row("重试次数", Input("", id="retries", placeholder="10", compact=True))
            yield from _field_row("数据块", Input("", id="blocksize", placeholder="1024 / 128", compact=True))
        with Horizontal(id="options-buttons"):
            yield Button("保存", id="save", variant="primary", compact=True)
            yield Button("取消", id="cancel", compact=True)


class OptionsScreen(AdaptiveModal):
    """Edit persisted options; on save they are applied immediately."""

    ROOT_ID = "options-box"
    # 富布局需要约 84 列（盒子宽 80 + 左右留白）与足够行高；更小则自动切到简洁模式
    MIN_WIDTH = 84
    MIN_HEIGHT = 22

    def build_rich(self) -> Vertical:
        return _OptionsRich(id=self.ROOT_ID)

    def build_compact(self) -> Vertical:
        return _OptionsCompact(id=self.ROOT_ID)

    # -- value plumbing (both layouts use the same widget ids) -----------------
    def after_build(self) -> None:
        cfg = self.app.cfg  # type: ignore[attr-defined]
        self.query_one("#echo", Checkbox).value = cfg.local_echo
        self.query_one("#wrap", Checkbox).value = cfg.wrap
        self.query_one("#rx_cr", Checkbox).value = cfg.rx_add_cr
        self.query_one("#rx_lf", Checkbox).value = cfg.rx_add_lf
        self.query_one("#ts", Checkbox).value = cfg.capture_timestamps
        self.query_one("#vt", Checkbox).value = cfg.send_vt_sequences
        self.query_one("#hex", Checkbox).value = cfg.hex_mode
        # 下拉框：仅当配置值合法时才选中它，否则回退到第一个选项
        enter = self.query_one("#enter", FieldSelect)
        enter.value = cfg.enter_sends if cfg.enter_sends in _ENTER_VALUES else _ENTER_OPTIONS[0][1]
        back = self.query_one("#back", FieldSelect)
        back.value = (
            cfg.backspace_sends if cfg.backspace_sends in _BACK_VALUES else _BACK_OPTIONS[0][1]
        )
        decode = self.query_one("#decode", FieldSelect)
        decode.value = cfg.decode if cfg.decode in _DECODE_VALUES else _DECODE_OPTIONS[0][1]
        self.query_one("#timeout", Input).value = str(cfg.xfer_timeout)
        self.query_one("#retries", Input).value = str(cfg.xfer_retries)
        self.query_one("#blocksize", Input).value = str(cfg.xfer_block_size)
        # 进入即选中第一项，方向键才能直接上下移动
        self.query_one("#echo", Checkbox).focus()

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
        cfg.hex_mode = self.query_one("#hex", Checkbox).value
        # 下拉框值必然合法，无需再校验
        cfg.enter_sends = str(self.query_one("#enter", FieldSelect).value)
        cfg.backspace_sends = str(self.query_one("#back", FieldSelect).value)
        cfg.decode = str(self.query_one("#decode", FieldSelect).value)
        try:
            cfg.xfer_timeout = float(self.query_one("#timeout", Input).value)
            cfg.xfer_retries = int(self.query_one("#retries", Input).value)
            cfg.xfer_block_size = int(self.query_one("#blocksize", Input).value)
        except ValueError:
            pass
        if cfg.xfer_block_size not in (128, 1024):
            cfg.xfer_block_size = 1024
        save_config(cfg)
        self.app.apply_config()  # type: ignore[attr-defined]
        self.dismiss(None)
