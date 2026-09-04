"""Serial connection / parameter dialog."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Input, Label, Select, Static

from pyterm.config import ConnectionSettings
from pyterm.screens.base import AdaptiveModal, FieldSelect
from pyterm.serialio import available_ports


class _ConnRich(Vertical):
    """Full layout: detected-port table plus parameter fields in a box."""

    def compose(self) -> ComposeResult:
        yield Static("串口连接参数", id="conn-title")
        yield DataTable(id="ports", cursor_type="row")
        with Horizontal(classes="form-row"):
            yield Label("波特率", classes="form-label")
            yield Input("", id="baud", placeholder="115200", compact=True)
            yield Label("数据位", classes="form-label")
            yield Input("", id="bytesize", placeholder="8", compact=True)
        with Horizontal(classes="form-row"):
            yield Label("校验", classes="form-label")
            yield Input("", id="parity", placeholder="N / E / O", compact=True)
            yield Label("停止位", classes="form-label")
            yield Input("", id="stopbits", placeholder="1 / 2", compact=True)
        with Horizontal(classes="form-row"):
            yield Label("流控", classes="form-label")
            yield Input("", id="flow", placeholder="none / rtscts / xonxoff", compact=True)
        with Horizontal(id="conn-buttons"):
            yield Button("刷新", id="refresh", compact=True)
            yield Button("连接", id="connect", variant="primary", compact=True)
            yield Button("返回", id="cancel", compact=True)
        yield Label("", id="conn-error")


class _ConnCompact(Vertical):
    """Simple layout for small windows: one full-width labelled field per row
    inside a scroll area, plus the port list shown as a dropdown."""

    def compose(self) -> ComposeResult:
        yield Static("串口连接参数", id="conn-title")
        with VerticalScroll(id="conn-body"):
            with Horizontal(classes="c-row"):
                yield Label("端口", classes="c-label")
                # 先放占位项避免空选项；实际端口列表在 after_build() 中填充
                yield FieldSelect(
                    [("检测串口…", "")],
                    id="port-sel",
                    allow_blank=False,
                    compact=True,
                    prompt="检测到的串口",
                )
            with Horizontal(classes="c-row"):
                yield Label("波特率", classes="c-label")
                yield Input("", id="baud", placeholder="115200", compact=True)
            with Horizontal(classes="c-row"):
                yield Label("数据位", classes="c-label")
                yield Input("", id="bytesize", placeholder="8", compact=True)
            with Horizontal(classes="c-row"):
                yield Label("校验", classes="c-label")
                yield Input("", id="parity", placeholder="N / E / O", compact=True)
            with Horizontal(classes="c-row"):
                yield Label("停止位", classes="c-label")
                yield Input("", id="stopbits", placeholder="1 / 2", compact=True)
            with Horizontal(classes="c-row"):
                yield Label("流控", classes="c-label")
                yield Input("", id="flow", placeholder="none / rtscts / xonxoff", compact=True)
        with Horizontal(id="conn-buttons"):
            yield Button("刷新", id="refresh", compact=True)
            yield Button("连接", id="connect", variant="primary", compact=True)
            yield Button("返回", id="cancel", compact=True)
        yield Label("", id="conn-error")


class ConnectionScreen(AdaptiveModal):
    """List detected ports, edit parameters and connect."""

    ROOT_ID = "conn-box"
    # 富布局（端口表 + 参数行）在高度 <30 时已放不下、无法使用，因此低于
    # 该高度（或宽度 <84）就自动切到可滚动的简洁模式。
    MIN_WIDTH = 84
    MIN_HEIGHT = 30

    def __init__(self) -> None:
        super().__init__()
        self._devices: list[tuple[str, str]] = []
        self._selected: str | None = None

    def build_rich(self) -> Vertical:
        return _ConnRich(id=self.ROOT_ID)

    def build_compact(self) -> Vertical:
        return _ConnCompact(id=self.ROOT_ID)

    def after_build(self) -> None:
        last = self.app.cfg.last  # type: ignore[attr-defined]
        self.query_one("#baud", Input).value = str(last.baudrate)
        self.query_one("#bytesize", Input).value = str(last.bytesize)
        self.query_one("#parity", Input).value = last.parity
        self.query_one("#stopbits", Input).value = str(int(last.stopbits))
        self.query_one("#flow", Input).value = last.flow
        if self._compact:
            self._fill_ports()
            self.query_one("#port-sel", FieldSelect).focus()
        else:
            self.query_one("#ports", DataTable).add_columns("端口", "描述")
            self._fill_ports()
            self.query_one("#ports", DataTable).focus()

    # -- helpers ---------------------------------------------------------------
    def _set_error(self, text: str) -> None:
        self.query_one("#conn-error", Label).update(text)

    def _device_names(self) -> list[str]:
        return [dev for dev, _ in self._devices]

    def _fill_ports(self) -> None:
        self._devices = available_ports()
        # 虚拟回环设备：默认隐藏，仅 --enable-debug 调试模式下提供（无需真实串口，纯回显）
        if getattr(self.app, "enable_debug", False):
            self._devices.append(("LOOPBACK", "虚拟回环（调试 - 纯回显）"))
        if self._compact:
            select = self.query_one("#port-sel", FieldSelect)
            select.set_options([(dev, dev) for dev in self._device_names()])
            names = self._device_names()
            chosen = self._selected if self._selected in names else names[0]
            select.value = chosen
            self._selected = str(select.value)
        else:
            table = self.query_one("#ports", DataTable)
            table.clear()
            for dev, desc in self._devices:
                table.add_row(dev, desc)
            if self._devices:
                self._selected = self._devices[0][0]

    def _read_settings(self) -> ConnectionSettings | None:
        app: Any = self.app
        base = deepcopy(app.cfg.last) if app.cfg else ConnectionSettings()
        try:
            base.port = self._selected or ""
            base.baudrate = int(self.query_one("#baud", Input).value or base.baudrate)
            base.bytesize = int(self.query_one("#bytesize", Input).value or base.bytesize)
            base.parity = (self.query_one("#parity", Input).value or base.parity).upper()[0]
            base.stopbits = float(self.query_one("#stopbits", Input).value or base.stopbits)
            base.flow = (self.query_one("#flow", Input).value or base.flow).lower()
        except (ValueError, IndexError):
            self._set_error("参数格式错误")
            return None
        if base.flow not in ("none", "rtscts", "xonxoff"):
            base.flow = "none"
        if base.parity not in ("N", "E", "O"):
            base.parity = "N"
        if not base.port:
            self._set_error("请先在列表中选择端口")
            return None
        return base

    # -- events ----------------------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "refresh":
            self._fill_ports()
        elif button_id == "connect":
            self._connect()
        elif button_id == "cancel":
            self.dismiss(None)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        index = self.query_one("#ports", DataTable).get_row_index(event.row_key)
        if 0 <= index < len(self._devices):
            self._selected = self._devices[index][0]

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._connect()

    def on_select_changed(self, event: Select.Changed) -> None:
        if getattr(event.select, "id", None) == "port-sel":
            self._selected = str(event.value)

    def _connect(self) -> None:
        settings = self._read_settings()
        if settings is None:
            return
        app: Any = self.app
        err = (
            app.open_loopback()  # type: ignore[attr-defined]
            if settings.port == "LOOPBACK"
            else app.open_serial(settings)  # type: ignore[attr-defined]
        )
        if err:
            self._set_error(f"连接失败: {err}")
            return
        app.refresh_status()
        self.dismiss(settings)
