"""Serial connection / parameter dialog."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Label, Static

from pyterm.config import ConnectionSettings
from pyterm.screens.base import ModalBase
from pyterm.serialio import available_ports


class ConnectionScreen(ModalBase):
    """List detected ports, edit parameters, connect / disconnect."""

    def __init__(self) -> None:
        super().__init__()
        self._devices: list[tuple[str, str]] = []
        self._selected: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="conn-box"):
            yield Static("串口连接参数", id="conn-title")
            table: DataTable = DataTable(id="ports", cursor_type="row")
            yield table
            with Horizontal(classes="form-row"):
                yield Label("波特率", classes="form-label")
                yield Input("", id="baud", placeholder="115200")
                yield Label("数据位", classes="form-label")
                yield Input("", id="bytesize", placeholder="8")
            with Horizontal(classes="form-row"):
                yield Label("校验", classes="form-label")
                yield Input("", id="parity", placeholder="N / E / O")
                yield Label("停止位", classes="form-label")
                yield Input("", id="stopbits", placeholder="1 / 2")
            with Horizontal(classes="form-row"):
                yield Label("流控", classes="form-label")
                yield Input("", id="flow", placeholder="none / rtscts / xonxoff")
                yield Button("刷新", id="refresh")
                yield Button("连接", id="connect", variant="primary")
                yield Button("断开", id="disconnect")
            yield Label("", id="conn-error")

    def on_mount(self) -> None:
        table = self.query_one("#ports", DataTable)
        table.add_columns("端口", "描述")
        last = self.app.cfg.last  # type: ignore[attr-defined]
        self.query_one("#baud", Input).value = str(last.baudrate)
        self.query_one("#bytesize", Input).value = str(last.bytesize)
        self.query_one("#parity", Input).value = last.parity
        self.query_one("#stopbits", Input).value = str(int(last.stopbits))
        self.query_one("#flow", Input).value = last.flow
        self._fill_ports()
        table.focus()

    # -- helpers ---------------------------------------------------------------
    def _set_error(self, text: str) -> None:
        self.query_one("#conn-error", Label).update(text)

    def _fill_ports(self) -> None:
        self._devices = available_ports()
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
        elif button_id == "disconnect":
            self.app.close_serial()  # type: ignore[attr-defined]
            self.app.refresh_status()  # type: ignore[attr-defined]
            self._set_error("已断开")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        index = self.query_one("#ports", DataTable).get_row_index(event.row_key)
        if 0 <= index < len(self._devices):
            self._selected = self._devices[index][0]

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._connect()

    def _connect(self) -> None:
        settings = self._read_settings()
        if settings is None:
            return
        err = self.app.open_serial(settings)  # type: ignore[attr-defined]
        if err:
            self._set_error(f"连接失败: {err}")
            return
        self.app.refresh_status()  # type: ignore[attr-defined]
        self.dismiss(settings)
