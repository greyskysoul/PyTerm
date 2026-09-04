"""PyTerm application — minicom-style serial terminal with YMODEM transfer."""

from __future__ import annotations

import argparse
import codecs
import contextlib
import os
import queue
import shutil
import sys
import threading
import time
from copy import deepcopy
from importlib import resources as _resources
from typing import Any, ClassVar

from rich.style import Style
from rich.text import Text as RichText
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.events import Key
from textual.widgets import Button, TextArea

from pyterm import APP_NAME, __version__
from pyterm.config import AppConfig, ConnectionSettings, load_config, save_config
from pyterm.keys import (
    KeyMapper,
    decode_escapes,
    format_hex_lines,
    hex_bytes_per_line,
    parse_hex_line,
)
from pyterm.screens.base import ConfirmDialog
from pyterm.screens.connection import ConnectionScreen
from pyterm.screens.help import MainMenuScreen
from pyterm.screens.options import OptionsScreen
from pyterm.screens.transfer import RecvScreen, SendScreen
from pyterm.serialio import SerialManager
from pyterm.termdisplay.view import StatusBar, TerminalView
from pyterm.termdisplay.vt import TerminalModel
from pyterm.xfer.ymodem import YModemEngine

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")

# Vertical-bar cursor shown in the 16-hex editor while it is not focused.
_HEX_BAR = "\u2502"
_HEX_BAR_STYLE = Style(color="#9aa7b8")

# 主窗口最小可用终端尺寸：低于该值时界面“完全无法使用”，启动/运行时会打印
# 提示并直接退出（而不是渲染一个残破、无法操作的界面）。
MIN_TERMINAL_COLS = 20
MIN_TERMINAL_ROWS = 5
# app.run() 因窗口过小退出时的返回值，供 main() 识别并打印提示。
_EXIT_TOO_SMALL = "too-small"


def _too_small_message(cols: int, rows: int) -> str:
    return (
        f"PyTerm: 终端窗口太小（{cols} 列 × {rows} 行），界面无法正常使用。\n"
        f"请将窗口放大到至少 {MIN_TERMINAL_COLS} 列 × {MIN_TERMINAL_ROWS} 行后重新运行。\n"
    )


def _linear_to_location(text: str, idx: int) -> tuple[int, int]:
    """Convert a linear character index into a (row, column) location."""
    lines = text.split("\n")
    for row, line in enumerate(lines):
        if idx <= len(line):
            return (row, idx)
        idx -= len(line) + 1
    last = len(lines) - 1
    return (last, len(lines[last]))


def _location_after_hex(text: str, hex_count: int) -> tuple[int, int]:
    """Location just after the ``hex_count``-th hex digit of ``text``."""
    if hex_count <= 0:
        return (0, 0)
    seen = 0
    for i, ch in enumerate(text):
        if ch in _HEX_DIGITS:
            seen += 1
            if seen == hex_count:
                return _linear_to_location(text, i + 1)
    return _linear_to_location(text, len(text))


class _HexArea(TextArea):
    """Multi-line 16-hex editor used by the HEX mode input bar.

    * only hex digits are kept (anything else is stripped while typing),
    * a single space is inserted after every byte,
    * each line holds 4/8/16 bytes depending on the current width.
    """

    BINDINGS: ClassVar[list] = [
        b
        for b in TextArea.BINDINGS
        if not (isinstance(b, Binding) and "ctrl+a" in b.key.split(","))
    ]

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("show_line_numbers", False)
        super().__init__(**kwargs)
        self._reformatting = False
        self.cursor_blink = False  # 常亮的块状光标，不闪烁

    def on_text_area_changed(self, _event: TextArea.Changed) -> None:
        self._reflow(keep_cursor=True)

    def on_resize(self) -> None:
        if self.text:
            self._reflow(keep_cursor=False)

    def _watch_has_focus(self, focus: bool) -> None:
        # 聚焦/失焦时丢弃行缓存并重绘：失焦要画出“竖线”光标，聚焦要恢复实心块。
        super()._watch_has_focus(focus)
        self._line_cache.clear()
        self.refresh()

    def get_line(self, line_index: int) -> RichText:
        """Render one document line; while not focused, draw the cursor as a
        vertical bar (the focused cursor stays TextArea's own block)."""
        line = super().get_line(line_index)
        if self.has_focus:
            return line
        row, col = self.cursor_location
        if row != line_index:
            return line
        bar = RichText(_HEX_BAR, style=_HEX_BAR_STYLE)
        if col >= len(line):
            return RichText.assemble(line, bar)
        return RichText.assemble(line[:col], bar, line[col + 1 :])

    def _reflow(self, keep_cursor: bool) -> None:
        """Rebuild the document in canonical form: only hex, spaced per byte."""
        if self._reformatting:
            return
        raw = self.text or ""
        digits = "".join(c for c in raw if c in _HEX_DIGITS).upper()
        target = self._hex_digits_before_cursor(raw)
        formatted = format_hex_lines(digits, hex_bytes_per_line(max(1, self.size.width)))
        if formatted == raw:
            return
        self._reformatting = True
        try:
            self.text = formatted
        finally:
            self._reformatting = False
        location = (
            _location_after_hex(formatted, target)
            if keep_cursor
            else _linear_to_location(formatted, len(formatted))
        )
        self.cursor_location = location  # type: ignore[assignment]

    def _hex_digits_before_cursor(self, raw: str) -> int:
        row, col = self.cursor_location
        lines = raw.split("\n")
        prefix = sum(len(line) + 1 for line in lines[:row]) + col
        prefix = min(prefix, len(raw))
        return sum(1 for ch in raw[:prefix] if ch in _HEX_DIGITS)


class _HexBar(Vertical):
    """Bottom 16-hex input bar; mounted only while HEX mode is on.

    Keeping it out of the DOM when HEX is off prevents hidden focusable widgets
    from stealing focus (which used to break Ctrl+A and other combos).
    """

    def compose(self) -> ComposeResult:
        yield _HexArea(id="hex-input")
        yield Button("发送", id="hex-send", compact=True)


class _StatusMenuButton(Button, can_focus=False):
    """Bottom-right "菜单" button on the main window.

    Equivalent to Ctrl+A Z.  ``can_focus`` is disabled so it never steals the
    keyboard focus: typed keys keep going straight to the serial port and the
    Ctrl+A prefix handling is unaffected — the button is mouse-click only.
    """


_PREFIX_FUNCS = {
    "z": "主菜单",
    "x": "退出",
    "s": "发送文件",
    "r": "接收文件",
    "c": "清屏",
    "l": "捕获开/关",
    "h": "16进制 开/关",
    "p": "串口参数",
    "o": "选项",
}


def _load_css() -> str:
    """Read app.tcss from package resources (works frozen & editable)."""
    try:
        return _resources.files("pyterm.resources").joinpath("app.tcss").read_text(encoding="utf-8")
    except Exception:
        return ""


CSS_CONTENT = _load_css()


class _QueueIO:
    """Adapt a ``queue.Queue`` + SerialManager to the YModemEngine read/write API."""

    def __init__(
        self, q: queue.Queue[bytes], serial: SerialManager, cancel: threading.Event
    ) -> None:
        self.q = q
        self.serial = serial
        self.cancel = cancel

    def read(self, timeout: float) -> bytes | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.cancel.is_set():
                return None
            try:
                data = self.q.get(timeout=min(0.2, deadline - time.monotonic()))
                return data
            except queue.Empty:
                continue
        return None

    def write(self, data: bytes) -> None:
        self.serial.write(data)


class PyTermApp(App):
    """The terminal main window.  Handles the Ctrl+A prefix key model."""

    CSS = CSS_CONTENT
    TITLE = f"PyTerm v{__version__}"
    SUB_TITLE = "串口终端 - YMODEM"

    def __init__(
        self,
        cfg: AppConfig | None = None,
        cli_conn: ConnectionSettings | None = None,
        exit_idle: float | None = None,
        startup_text: str | None = None,
        startup_script: str | None = None,
    ) -> None:
        super().__init__()
        self.cfg = cfg or AppConfig()
        self.cli_conn = cli_conn
        self.exit_idle = max(0.0, float(exit_idle)) if exit_idle is not None else None
        self.startup_text = startup_text
        self.startup_script = startup_script

        self.model = TerminalModel(80, 24, scrollback=self.cfg.scrollback, decode=self.cfg.decode)
        self.model.rx_add_cr = self.cfg.rx_add_cr
        self.model.rx_add_lf = self.cfg.rx_add_lf

        self.serial = SerialManager(on_data=self._on_rx, on_error=self._on_serial_error)
        self.mapper = KeyMapper(self.cfg)

        self._prefix = False
        self._tx = 0
        self._rx = 0
        self._enter_presses = 0
        self._last_rx = time.monotonic()
        self._startup_thread: threading.Thread | None = None
        self._loopback = False  # virtual echo device (no real port)
        # HEX 接收显示：当前显示行已排的字节数（跨接收块持续计数，用于按
        # 窗口宽度在 4/8/16/32 字节处连续换行）
        self._hex_row_bytes = 0

        # 启动/连接时打印到终端的本地提示（橙/粗体），布局稳定后统一刷出
        self._pending_hints: list[str] = []
        self._hint_flush_pending = False

        # capture file
        self._capture_fh: Any = None
        self._cap_decoder: Any = None
        self._at_line_start = True

        # transfer state (owned by worker thread)
        self._xfer_queue: queue.Queue[bytes] | None = None
        self._xfer_cancel = threading.Event()
        self._xfer_ui: Any = None  # screen that shows progress
        self._xfer_thread: threading.Thread | None = None

        # too-small terminal guard (see _guard_minimum_size)
        self._too_small = False
        self._too_small_size = (0, 0)

    # ====================================================================== compose
    def compose(self):
        with Container(id="term-root"):
            yield TerminalView(self.model, id="term")
            with Horizontal(id="bottom"):
                # “菜单”按钮在左下角；状态文字占满其余宽度
                yield _StatusMenuButton("菜单", id="menu-btn")
                yield StatusBar("", id="status")

    # ------------------------------------------------------ minimum-size guard
    def _terminal_too_small(self) -> bool:
        """True when the terminal is smaller than the usable floor."""
        w, h = self.size.width, self.size.height
        if w <= 0 or h <= 0:
            return False  # layout not done yet
        return w < MIN_TERMINAL_COLS or h < MIN_TERMINAL_ROWS

    def _guard_minimum_size(self) -> None:
        """Terminal far too small to be usable: stop the app; the CLI prints a
        hint afterwards (see main()).  Safe to call repeatedly — only the first
        detection triggers the exit."""
        if self._too_small or not self._terminal_too_small():
            return
        self._too_small = True
        self._too_small_size = (self.size.width, self.size.height)
        self.exit(_EXIT_TOO_SMALL)

    def on_mount(self) -> None:
        self._guard_minimum_size()
        if self._too_small:
            return  # exiting — do not start timers/bootstrap
        self.set_interval(0.4, self._tick)
        # compose children are not mounted yet — retry until the DOM is ready
        self._bootstrap_attempt()

    def on_resize(self, _event=None) -> None:
        self._guard_minimum_size()

    def _bootstrap_attempt(self) -> None:
        try:
            self._view()
            self._status()
        except Exception:
            self.set_timer(0.05, self._bootstrap_attempt)
            return
        self._bootstrap()

    def _bootstrap(self) -> None:
        self._view().focus()
        self.apply_config()
        # 程序一启动就打印菜单快捷键提示（无论是否连接端口）
        self._print_startup_hint()
        if self.cli_conn is not None:
            err = self.open_serial(self.cli_conn)
            if err:
                self.notify(f"连接失败: {err}", severity="error")
            else:
                self.notify(f"已连接 {self.cli_conn.short()}")
                self._start_startup_send()
        self._refresh_status()

    # ------------------------------------------------------------- CLI startup sends
    def _start_startup_send(self) -> None:
        """(CLI) send the -s string / -f script once the port is open."""
        if not (self.startup_text or self.startup_script):
            return
        self._startup_thread = threading.Thread(
            target=self._run_startup_send, name="pyterm-startup-send", daemon=True
        )
        self._startup_thread.start()

    def _startup_busy(self) -> bool:
        return self._startup_thread is not None and self._startup_thread.is_alive()

    def _run_startup_send(self) -> None:
        """Run on a worker thread: send the -s string, then the -f script lines."""
        try:
            if self.startup_text:
                self._startup_write(decode_escapes(self.startup_text))
            if self.startup_script:
                with open(self.startup_script, encoding=self.cfg.decode) as fh:
                    lines = fh.read().splitlines()
                for raw in lines:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    self._startup_write(decode_escapes(line))
                    time.sleep(0.2)
        except OSError as exc:
            self.call_from_thread(self.notify, f"启动发送失败: {exc}", severity="error")
        finally:
            self._startup_thread = None

    def _startup_write(self, data: bytes) -> None:
        if data and self.serial.is_open:
            self.serial.write(data)

    # ======================================================================= helpers
    def _view(self) -> TerminalView:
        return self.query_one("#term", TerminalView)

    def _status(self) -> StatusBar:
        return self.query_one("#status", StatusBar)

    # =================================================================== serial io
    def is_connected(self) -> bool:
        return self._loopback or self.serial.is_open

    def open_serial(self, settings: ConnectionSettings) -> str | None:
        if self._xfer_thread is not None and self._xfer_thread.is_alive():
            return "请先完成/取消进行中的文件传输"
        # 离开虚拟回环模式：一旦要打开真实串口，发送必须走该端口而不是回环。
        # 与 open_loopback()（关闭真实串口并把 _loopback 置 True）保持对称，
        # 否则 LOOPBACK → 真实串口 切换后 _loopback 仍为 True，发送会被回环
        # 截走、状态栏也一直显示“虚拟回环”，看起来就像“切换不成功”。
        self._loopback = False
        err = self.serial.open(settings)
        if err is None:
            self.cfg.last = settings
            save_config(self.cfg)
            self._last_rx = time.monotonic()
            self._enter_presses = 0
            self._print_local_hint(self._connected_hint())
            self._refresh_status()
        return err

    def open_loopback(self) -> str | None:
        """Connect the virtual loopback device: every byte sent is echoed back."""
        if self._xfer_thread is not None and self._xfer_thread.is_alive():
            return "请先完成/取消进行中的文件传输"
        if self.serial.is_open:
            self.serial.close()
        self._loopback = True
        self._last_rx = time.monotonic()
        self._enter_presses = 0
        self._print_local_hint(self._connected_hint())
        self._refresh_status()
        return None

    # --------------------------------------------- 屏幕上的本地提示（橙/粗体）
    _STARTUP_HINT = "按 Ctrl+A Z 打开功能菜单"

    def _connected_hint(self) -> str:
        name = "虚拟回环" if self._loopback else self.cfg.last.short()
        return f"已连接 {name} · 直接键入即发送"

    def _print_local_hint(self, text: str) -> None:
        """排队一条本地提示（橙/粗体），等布局稳定后统一打印。

        本地提示不计入 TX/RX、不写入捕获文件；HEX 模式下不打印。延迟一小
        帧再刷出，避免启动阶段的首次布局/缩放把刚写入的提示清掉。
        """
        if self.cfg.hex_mode:
            return
        self._pending_hints.append(text)
        self._schedule_hint_flush()

    def _schedule_hint_flush(self) -> None:
        if self._hint_flush_pending:
            return
        self._hint_flush_pending = True
        try:
            self.set_timer(0.05, self._flush_hints)
        except Exception:
            self._hint_flush_pending = False

    def _flush_hints(self) -> None:
        self._hint_flush_pending = False
        if not self._pending_hints:
            return
        pending = self._pending_hints
        self._pending_hints = []
        data = b"".join(
            f"\x1b[1m\x1b[38;2;255;165;0m{t}\x1b[0m\r\n".encode(
                self.model.decode, "replace"
            )
            for t in pending
        )
        self.model.feed_bytes(data)
        with contextlib.suppress(Exception):
            self._view().mark_dirty()

    def _flush_hints_now(self) -> None:
        """在发送/显示真实串口内容前立即刷出尚未打印的本地提示，保证提示
        总是先于设备回显/接收数据出现。"""
        self._flush_hints()

    def _print_startup_hint(self) -> None:
        """程序启动即显示菜单快捷键提示（无论有没有连接端口）。"""
        self._print_local_hint(self._STARTUP_HINT)

    _NO_PORT_MSG = "未连接端口：请按 Ctrl+A P 连接后再试"

    def _remind_connect(self) -> None:
        """Toast shown when a send is attempted with no port connected."""
        self.notify(self._NO_PORT_MSG, severity="warning", timeout=6)

    # ------------------------------------------------------------- HEX send/recv bar
    def _hex_bar(self) -> Vertical:
        return self.query_one("#hex-bar", Vertical)

    def _sync_hex_ui(self) -> None:
        """Mount the hex input row only while HEX mode is on; remove it when off.
        (A permanently hidden focusable row used to steal focus and break
        Ctrl+A / other combos in the normal mode.)"""
        present = len(self.query("#hex-bar")) > 0
        if self.cfg.hex_mode:
            if not present:
                self._hex_row_bytes = 0  # 重新进入 HEX 模式：从头开始计行
                self.query_one("#term-root").mount(_HexBar(id="hex-bar"), before="#bottom")
        elif present:
            with contextlib.suppress(Exception):
                self.query_one("#hex-bar", Vertical).remove()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "menu-btn":
            event.stop()
            self.push_screen(MainMenuScreen())
            return
        if button_id != "hex-send":
            return
        event.stop()
        self._send_hex_box()

    def _send_hex_box(self) -> None:
        """Parse the 16-hex input and transmit it."""
        field = self.query_one("#hex-input", TextArea)
        raw = field.text.strip()
        if not raw:
            self.notify("请先在 16 进制输入框输入字节", severity="warning")
            return
        try:
            data = parse_hex_line(raw)
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        if not self.is_connected():
            self._remind_connect()
            return
        self.send_bytes(data)  # handles TX counter / local echo / loopback echo
        # 发送后保留输入内容（便于重复发送/追加），仅把焦点还给输入框
        field.focus()

    def close_serial(self) -> None:
        self.cancel_transfer()
        self._loopback = False
        self.serial.close()
        self._stop_capture()
        self._refresh_status()

    def send_bytes(self, data: bytes) -> bool:
        """Transmit bytes.  On the virtual loopback the data is echoed back as
        RX (a pure loopback device), otherwise it goes to the real port."""
        self._flush_hints_now()  # 先打印待刷出的本地提示
        if not data:
            return False
        if self._loopback:
            # 像真实终端那样回显：单独的 \r 回显为 \r\n（回车换行），
            # 已带 \n 的 \r\n 保持原样，避免按回车后仍停在原行覆盖内容。
            echoed = data.replace(b"\r\n", b"\r").replace(b"\r", b"\r\n")
            self._tx += len(data)
            self._rx += len(echoed)
            self._rx_to_terminal(echoed)
            return True
        ok = self.serial.write(data)
        if ok:
            self._tx += len(data)
            if self.cfg.local_echo:
                self._rx_to_terminal(data)
        return ok

    def _on_serial_error(self, message: str) -> None:
        self.call_from_thread(self._handle_serial_error, message)

    def _handle_serial_error(self, message: str) -> None:
        self.close_serial()
        self._refresh_status()
        self.notify(message, severity="error", timeout=8)

    # -- receive path -------------------------------------------------------------------
    def _on_rx(self, data: bytes) -> None:  # runs on the reader thread
        q = self._xfer_queue
        if q is not None:
            q.put(data)
        else:
            self._rx += len(data)
            self.call_from_thread(self._rx_to_terminal, data)

    def _rx_to_terminal(self, data: bytes) -> None:
        self._flush_hints_now()  # 接收数据显示前先打印待刷出的本地提示
        self._write_capture(data)
        self._last_rx = time.monotonic()
        if self.cfg.hex_mode:
            # 16 进制接收：真实 0A/0D 只是普通数据，显示为 "0A"/"0D"，不当作
            # 换行断行；行只在累计满 N 字节（按窗口宽度取 32/16/8/4）时换行。
            text = self._format_hex_rx(data)
            if text:
                self.model.feed_bytes(text.encode("ascii", "replace"))
        else:
            self.model.feed_bytes(data)
        self._view().mark_dirty()

    def _hex_rx_per_line(self) -> int:
        """每行多少字节随终端显示宽度自适应（32/16/8/4）。"""
        return hex_bytes_per_line(max(1, self.model.columns), max_bytes=32)

    def _format_hex_rx(self, data: bytes) -> str:
        """把一段接收数据排版为“每行 N 字节”的十六进制文本。

        发送区(输入框)之所以能连续按宽度换行，是因为它每次把整篇文本重排；
        而接收数据是分块到达的，若只在单个块内分组，小于 N 字节的小块会一直
        堆在同一行、长时间不换行。这里用 ``self._hex_row_bytes`` 跨数据块持续
        计数，无论块多大都严格在每满 N 字节处换行，从而与发送区一致地连续
        自动换行。文本用 ``\\r\\n`` 换行，保证每行回到列首。
        """
        if not data:
            return ""
        per_line = self._hex_rx_per_line()
        used = self._hex_row_bytes
        if used >= per_line:  # 行宽变化后旧计数失效，重新从行首排
            used = 0
        out: list[str] = []
        # 若本行已有内容（含切到 HEX 前文本模式留下的内容），先补一个空格，
        # 避免上一数据块末尾字节与下一数据块首字节粘在一起（如 "42"+"0D"）。
        if self.model.mid_line():
            out.append(" ")
        i, n = 0, len(data)
        while i < n:
            seg = data[i : i + (per_line - used)]
            i += len(seg)
            out.append(" ".join(f"{b:02X}" for b in seg))
            used += len(seg)
            if used >= per_line:
                out.append("\r\n")  # 该行已满：换到下一行行首
                used = 0
        self._hex_row_bytes = used
        return "".join(out)

    # ==================================================================== key handling
    async def _on_key(self, event: Key) -> None:
        # 任意键按下即关闭左侧的 toast 提示（未连接端口 / HEX 模式等）
        if self._notifications:
            self.clear_notifications()
        if len(self.screen_stack) > 1:
            # A modal is on top: its widgets / screen bindings have already
            # processed this key.  Do NOT fall through to app-level bindings —
            # that would re-run e.g. Tab/shift+Tab focus navigation a second
            # time and make focus jump two widgets per key press.
            return
        if self._prefix:
            self._prefix_second(event)
            event.stop()
            return
        if event.key == "ctrl+a":
            event.stop()
            self._enter_prefix()
            return
        if self.cfg.hex_mode:
            # 16 进制模式：主界面按键不再直接发送，只能经底部 16 进制框发送
            event.stop()
            return
        data = self.mapper.map(event.key, event.character)
        if data is not None:
            event.stop()
            ok = self.send_bytes(data)
            if event.key in ("enter", "return"):
                if not ok:
                    self._enter_presses += 1
                    if self._enter_presses >= 3:
                        self._enter_presses = 0
                        self._remind_connect()
                else:
                    self._enter_presses = 0
            else:
                self._enter_presses = 0
        else:
            self._enter_presses = 0

    # -- Ctrl+A prefix model --------------------------------------------------------------
    def _enter_prefix(self) -> None:
        self._prefix = True
        # 焦点若在 HEX 输入条上，先清空焦点，否则前缀后的字母会被输入框吞掉
        # （TerminalView 不可聚焦，focus() 不生效，只能 set_focus(None)）
        if self.cfg.hex_mode:
            with contextlib.suppress(Exception):
                focused = self.focused
                if focused is not None and focused.id in ("hex-input", "hex-send"):
                    self.screen.set_focus(None)
        self._status().update(
            "前缀模式：Z 菜单 / S 发送 / R 接收 / H HEX / P 串口 / O 选项 / X 退出 / Esc 取消"
        )

    def _cancel_prefix(self) -> None:
        self._prefix = False
        self._refresh_status()

    def _prefix_second(self, event: Key) -> None:
        self._prefix = False
        if event.key in ("escape", "ctrl+a"):
            # escape or a second Ctrl+A cancels the prefix (no byte is sent)
            self._refresh_status()
            return
        char = (event.character or "").lower()
        if char in _PREFIX_FUNCS:
            self.menu_action(char)
            return
        # unknown keys fall through to the device like a plain key press
        data = self.mapper.map(event.key, event.character)
        if data is not None and self.serial.is_open:
            self.send_bytes(data)
        self._refresh_status()

    # ======================================================================== actions
    def menu_action(self, code: str) -> None:
        """Dispatch a single-letter menu action (also from the main-menu overlay)."""
        if code == "z":
            self.push_screen(MainMenuScreen())
        elif code == "x":
            self.push_screen(
                ConfirmDialog("退出", "确定要退出 PyTerm 吗？"),
                callback=lambda yes: self.exit() if yes else None,
            )
        elif code == "s":
            if not self.is_connected():
                self._remind_connect()
            else:
                self.push_screen(SendScreen())
        elif code == "r":
            if not self.is_connected():
                self._remind_connect()
            else:
                self.push_screen(RecvScreen())
        elif code == "c":
            self.clear_terminal()
        elif code == "l":
            self.toggle_capture()
        elif code == "h":
            self.cfg.hex_mode = not self.cfg.hex_mode
            self.apply_config()
            if self.cfg.hex_mode:
                # 快捷键开启后自动聚焦 16 进制输入框，可直接输入
                self.set_timer(0.05, self._focus_hex_field)
            self.notify(
                "HEX 模式已开启：在底部输入框输入，点“发送”"
                if self.cfg.hex_mode
                else "HEX 模式已关闭"
            )

        elif code == "p":
            self.push_screen(ConnectionScreen())
        elif code == "o":
            self.push_screen(OptionsScreen())

    def _focus_hex_field(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#hex-input", TextArea).focus()

    def clear_terminal(self) -> None:
        """清屏：同时复位 TX/RX 字节计数器与 HEX 行内计数，并刷新状态栏。

        清屏代表显示区域重新开始，状态栏里的收发计数也随之从 0 累计，
        便于按“屏/页”衡量一次会话的数据量。
        """
        self.model.clear()
        self._tx = 0
        self._rx = 0
        self._hex_row_bytes = 0  # 清屏后 HEX 行计数从头开始
        self._view().mark_dirty()
        self._refresh_status()

    # =========================================================================== config
    def apply_config(self) -> None:
        self.mapper.refresh(self.cfg)
        self.model.set_decode(self.cfg.decode)
        self.model.rx_add_cr = self.cfg.rx_add_cr
        self.model.rx_add_lf = self.cfg.rx_add_lf
        self._view().mark_dirty()
        self._sync_hex_ui()
        self._refresh_status()

    # ============================================================================ status
    def _status_text(self) -> str:
        conn = "虚拟回环" if self._loopback else self.cfg.last.short()
        state = "已连接" if self.is_connected() else "未连接"
        flags = []
        if self._loopback:
            flags.append("回环")
        if self.is_connected():
            flags.append(f"TX {self._tx:,}")
            flags.append(f"RX {self._rx:,}")
        if self._capture_fh is not None:
            flags.append("捕获")
        if self.cfg.local_echo:
            flags.append("回显")
        if self.cfg.wrap:
            flags.append("回绕")
        if self.cfg.hex_mode:
            flags.append("HEX")
        if self._prefix:
            right = ""
        elif self.cfg.hex_mode:
            right = "HEX：底部输入，点发送"
        else:
            # Ctrl+A Z 提示已改为右下角的“菜单”按钮，状态栏不再重复显示
            right = ""
        mid = "  ".join(flags)
        return f" {conn} | {state} | {mid}".rstrip(" |") + (f"    {right}" if right else "")

    def _refresh_status(self) -> None:
        with contextlib.suppress(Exception):  # DOM not ready / already torn down
            self._status().update(self._status_text())
        self._sync_view_active()

    def _sync_view_active(self) -> None:
        """Tell the terminal view whether it is the active typing target: only
        on the main screen with HEX mode off (keys then reach the terminal), a
        filled-block cursor is shown; otherwise the cursor turns hollow."""
        with contextlib.suppress(Exception):
            self._view().active = len(self.screen_stack) == 1 and not self.cfg.hex_mode

    def refresh_status(self) -> None:
        """Public entry point used by screens/dialogs to refresh the status bar."""
        self._refresh_status()

    def _tick(self) -> None:
        self._check_idle_exit()
        if self._prefix:
            return
        self._refresh_status()

    def _check_idle_exit(self) -> None:
        """(-e) exit when no byte has been received for ``exit_idle`` seconds."""
        limit = self.exit_idle
        if (
            limit is None
            or self._transfer_busy()
            or self._startup_busy()
            or self._prefix
        ):
            return
        if time.monotonic() - self._last_rx >= limit:
            self.exit()

    # ============================================================================ capture
    def toggle_capture(self) -> None:
        if self._capture_fh is not None:
            self._stop_capture()
            return
        path = self.cfg.capture_path
        if not path:
            path = f"pyterm-capture-{time.strftime('%Y%m%d-%H%M%S')}.log"
        try:
            fh = open(path, "a", encoding="utf-8", newline="")  # noqa: SIM115 persistent handle
        except OSError as exc:
            self.notify(f"无法创建捕获文件: {exc}", severity="error")
            return
        self._capture_fh = fh
        self.cfg.capture_path = path
        save_config(self.cfg)
        self._cap_decoder = codecs.getincrementaldecoder(self.cfg.decode)(errors="replace")
        self._at_line_start = True
        self.notify(f"开始捕获到 {path}")
        self._refresh_status()

    def _stop_capture(self, refresh: bool = True) -> None:
        if self._capture_fh is not None:
            with contextlib.suppress(Exception):
                self._capture_fh.close()  # type: ignore[union-attr]
        self._capture_fh = None
        if refresh:
            self._refresh_status()

    def _write_capture(self, data: bytes) -> None:
        if self._capture_fh is None:
            return
        try:
            text = self._cap_decoder.decode(data)  # type: ignore[union-attr]
        except Exception:
            return
        if not text:
            return
        if self.cfg.capture_timestamps:
            ts = time.strftime("%H:%M:%S")
            parts = text.split("\n")
            if self._at_line_start and parts and parts[0]:
                parts[0] = f"[{ts}] {parts[0]}"
            for i in range(1, len(parts)):
                if parts[i]:
                    parts[i] = f"[{ts}] {parts[i]}"
            text = "\n".join(parts)
            self._at_line_start = text.endswith("\n")
        self._capture_fh.write(text)  # type: ignore[union-attr]
        self._capture_fh.flush()  # type: ignore[union-attr]

    # ====================================================================== transfers
    def _transfer_busy(self) -> bool:
        return self._xfer_thread is not None and self._xfer_thread.is_alive()

    def _guard_transfer(self) -> str | None:
        if not self.is_connected():
            return "未连接串口"
        if self._transfer_busy():
            return "已有文件传输正在进行"
        return None

    def start_transfer_send(self, path: str) -> str | None:
        err = self._guard_transfer()
        if err:
            return err
        if not os.path.isfile(path):
            return f"文件不存在: {path}"
        self.cfg._last_send_file = path  # type: ignore[attr-defined]

        self._xfer_queue = queue.Queue()
        self._xfer_cancel.clear()
        self._xfer_ui = self.screen_stack[-1] if len(self.screen_stack) > 1 else None
        self._xfer_thread = threading.Thread(
            target=self._run_send, args=(path,), name="pyterm-ymodem-send", daemon=True
        )
        self._xfer_thread.start()
        return None

    def start_transfer_recv(self, directory: str, name_override: str = "") -> str | None:
        err = self._guard_transfer()
        if err:
            return err
        self._xfer_queue = queue.Queue()
        self._xfer_cancel.clear()
        self._xfer_ui = self.screen_stack[-1] if len(self.screen_stack) > 1 else None
        self._xfer_thread = threading.Thread(
            target=self._run_recv,
            args=(directory, name_override),
            name="pyterm-ymodem-recv",
            daemon=True,
        )
        self._xfer_thread.start()
        return None

    def cancel_transfer(self) -> None:
        self._xfer_cancel.set()

    def _xfer_emit(self, method: str, *args) -> None:
        ui = self._xfer_ui
        if ui is None:
            return
        with contextlib.suppress(Exception):
            self.call_from_thread(getattr(ui, method), *args)

    def _xfer_done(self) -> None:
        self._xfer_queue = None
        self._xfer_thread = None
        self._xfer_ui = None
        self._xfer_cancel.clear()
        self._refresh_status()

    def _engine(self, cb):
        assert self._xfer_queue is not None
        io = _QueueIO(self._xfer_queue, self.serial, self._xfer_cancel)
        return YModemEngine(
            io.read,
            io.write,
            timeout=self.cfg.xfer_timeout,
            retries=self.cfg.xfer_retries,
            block_size=self.cfg.xfer_block_size,
            cancel=self._xfer_cancel,
            cb=cb,
        )

    def _run_send(self, path: str) -> None:
        name = os.path.basename(path)

        def cb(phase: str, fname: str, sent: int, total) -> None:
            self._xfer_emit("show_progress", phase, fname, sent, total)

        try:
            with open(path, "rb") as fh:
                ok, msg = self._engine(cb).send(fh, filename=name)
        except Exception as exc:
            ok, msg = False, str(exc)
        self._xfer_emit("show_result", ok, msg)
        self._xfer_done()

    def _run_recv(self, directory: str, name_override: str) -> None:
        def cb(phase: str, fname: str, sent: int, total) -> None:
            self._xfer_emit("show_progress", phase, fname, sent, total)

        def open_file(filename: str, size) -> object | None:
            from pyterm.screens.transfer import sanitize_filename

            fname = sanitize_filename(name_override or filename or "download.bin")
            path = os.path.join(directory, fname)
            try:
                return open(path, "wb")
            except OSError as exc:
                self._xfer_emit("show_result", False, f"无法写入 {path}: {exc}")
                return None

        try:
            ok, msg, fname = self._engine(cb).recv(open_file)
        except Exception as exc:
            ok, msg = False, str(exc)
        self._xfer_emit("show_result", ok, msg)
        self._xfer_done()

    # ============================================================================== meta
    def on_unmount(self) -> None:
        self.cancel_transfer()
        self.serial.close()
        self._stop_capture(refresh=False)


# =============================================================================== CLI
def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description=(
            "minicom 风格的跨平台串口终端：VT/ANSI 渲染、YMODEM 收发、"
            "16 进制接收/发送。\n"
            "不带参数启动即进入交互界面（Ctrl+A 打开功能菜单）。"
        ),
        epilog=(
            "示例：\n"
            "  pyterm -p COM3 -b 115200\n"
            '  pyterm -p COM3 -s "AT\\r"\n'
            "  pyterm -p COM3 -f boot.txt -e 5\n"
            "  pyterm -p COM3 --hex\n"
            "  pyterm --bare -p COM3 -b 115200   # 无界面纯直通：stdin→串口，串口→stdout\n"
            "\n"
            "-s/-f 内容支持 \\n \\r \\t \\xHH 等转义；-e 支持小数秒。"
            "--bare 隐藏全部界面，仅供外部进程（如 AI agent）通过标准输入输出驱动。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="help", help="显示本帮助并退出")
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="显示版本号并退出",
    )

    conn = parser.add_argument_group("连接参数")
    conn.add_argument("-p", "--port", metavar="PORT", default=None, help="串口，如 COM3 或 /dev/ttyUSB0")
    conn.add_argument("-b", "--baud", type=int, metavar="BAUD", default=None, help="波特率")
    conn.add_argument("--data-bits", type=int, metavar="5-8", default=None, help="数据位")
    conn.add_argument("--parity", metavar="N/E/O", default=None, help="校验位")
    conn.add_argument("--stop-bits", type=float, metavar="1|1.5|2", default=None, help="停止位")
    conn.add_argument("--flow", metavar="MODE", default=None, help="流控：none / rtscts / xonxoff")

    startup = parser.add_argument_group("启动动作")
    startup.add_argument(
        "-s", "--send", metavar="TEXT", default=None, help="连接后发送字符串命令（支持转义）"
    )
    startup.add_argument(
        "-f",
        "--script",
        metavar="FILE",
        default=None,
        help="连接后逐行发送脚本文件（# 开头为注释行）",
    )
    startup.add_argument(
        "-e",
        "--exit-idle",
        type=float,
        metavar="SECS",
        default=None,
        help="空闲自动退出：连续 SECS 秒未收到任何字节",
    )
    startup.add_argument("--hex", action="store_true", help="启动即开启 16 进制接收/发送")

    bridge = parser.add_argument_group("直通模式（--bare，无界面）")
    bridge.add_argument(
        "--bare",
        action="store_true",
        help="隐藏全部界面：把 stdin 接到串口（发送），串口 RX 原样打到 stdout。"
        "需通过 -p/--port 指定串口，适合把终端交给 AI agent 等外部进程驱动",
    )

    args = parser.parse_args(argv)
    if (args.send is not None or args.script is not None) and not args.port:
        parser.error("-s/--send、-f/--script 需要先通过 -p/--port 指定端口")
    if args.exit_idle is not None and args.exit_idle <= 0:
        parser.error("-e/--exit-idle 必须为正数")
    if args.bare and not args.port:
        parser.error("--bare 直通模式必须通过 -p/--port 指定串口")
    if args.bare and (args.send or args.script or args.hex or args.exit_idle is not None):
        parser.error("--bare 直通模式不能与 -s/-f/-e/--hex 等交互启动选项同时使用")
    return args


def _make_conn(cfg: AppConfig, args) -> ConnectionSettings | None:
    """Build the CLI connection settings from -p/-b/--parity/...; returns None
    when no connection option was given (interactive mode then starts idle)."""
    if not (args.port or args.baud):
        return None
    conn = deepcopy(cfg.last)
    if args.port:
        conn.port = args.port
    if args.baud:
        conn.baudrate = args.baud
    if args.data_bits:
        conn.bytesize = args.data_bits
    if args.parity:
        conn.parity = args.parity.upper()
    if args.stop_bits:
        conn.stopbits = args.stop_bits
    if args.flow:
        conn.flow = args.flow.lower()
    return conn


def _binary_stdio() -> None:
    """Make stdin/stdout byte-transparent on Windows (no CRLF translation)."""
    if os.name == "nt":
        with contextlib.suppress(Exception):
            import msvcrt

            msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
            msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)


def run_bare(args) -> int:
    """--bare entry point: a transparent, UI-less serial bridge.

    Every byte read from stdin is written to the port and every byte received
    on the port is written raw to stdout.  Nothing else is rendered, so an
    external process (e.g. an AI agent) can own this process's stdin/stdout
    pipes and talk straight to the device.
    """
    cfg = load_config()
    conn = _make_conn(cfg, args)
    assert conn is not None, "--bare requires -p/--port (enforced by argparse)"

    _binary_stdio()
    stop = threading.Event()

    def on_rx(data: bytes) -> None:  # serial reader thread
        try:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        except (OSError, ValueError):
            stop.set()

    def on_error(message: str) -> None:
        sys.stderr.write(f"pyterm: {message}\n")
        sys.stderr.flush()
        stop.set()

    mgr = SerialManager(on_data=on_rx, on_error=on_error)
    err = mgr.open(conn)
    if err:
        sys.stderr.write(f"pyterm: 连接失败: {err}\n")
        sys.stderr.flush()
        return 1
    sys.stderr.write(f"pyterm: bare 直通已连接 {conn.short()}（stdin→串口，串口 RX→stdout）\n")
    sys.stderr.flush()

    def pump_stdin() -> None:
        fd = sys.stdin.fileno()
        try:
            while not stop.is_set():
                data = os.read(fd, 4096)
                if not data:  # stdin EOF -> agent closed the pipe
                    break
                mgr.write(data)
        except (OSError, ValueError):
            pass  # console closed / interrupted

    stdin_thread = threading.Thread(target=pump_stdin, name="pyterm-bare-stdin", daemon=True)
    stdin_thread.start()
    try:
        while stdin_thread.is_alive() and not stop.is_set():
            stdin_thread.join(timeout=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        mgr.close()
    return 0


def main(argv=None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.bare:
        return run_bare(args)

    cfg = load_config()
    if args.hex:
        cfg.hex_mode = True  # 本次启动自动开启 16 进制接收/发送

    cli_conn = _make_conn(cfg, args)

    app = PyTermApp(
        cfg=cfg,
        cli_conn=cli_conn,
        exit_idle=args.exit_idle,
        startup_text=args.send,
        startup_script=args.script,
    )

    # 启动前检查终端尺寸：窗口小到完全无法使用时直接提示并退出，不进入 TUI。
    cols, rows = shutil.get_terminal_size()
    if cols < MIN_TERMINAL_COLS or rows < MIN_TERMINAL_ROWS:
        sys.stderr.write(_too_small_message(cols, rows))
        return 1

    result = app.run()
    if result == _EXIT_TOO_SMALL:  # 运行中窗口被缩到过小
        w, h = app._too_small_size
        sys.stderr.write(_too_small_message(w, h))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
