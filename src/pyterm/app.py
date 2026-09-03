"""PyTerm application — minicom-style serial terminal with YMODEM transfer."""

from __future__ import annotations

import argparse
import codecs
import contextlib
import os
import queue
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
from textual.containers import Container, Horizontal
from textual.events import Key
from textual.widgets import Button, TextArea

from pyterm import APP_NAME, __version__
from pyterm.config import AppConfig, ConnectionSettings, load_config, save_config
from pyterm.keys import (
    KeyMapper,
    decode_escapes,
    format_hex,
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


class _HexBar(Horizontal):
    """Bottom 16-hex input bar; mounted only while HEX mode is on.

    Keeping it out of the DOM when HEX is off prevents hidden focusable widgets
    from stealing focus (which used to break Ctrl+A and other combos).
    """

    def compose(self) -> ComposeResult:
        yield _HexArea(id="hex-input")
        yield Button("发送", id="hex-send", compact=True)


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

        # capture file
        self._capture_fh: Any = None
        self._cap_decoder: Any = None
        self._at_line_start = True

        # transfer state (owned by worker thread)
        self._xfer_queue: queue.Queue[bytes] | None = None
        self._xfer_cancel = threading.Event()
        self._xfer_ui: Any = None  # screen that shows progress
        self._xfer_thread: threading.Thread | None = None

    # ====================================================================== compose
    def compose(self):
        with Container(id="term-root"):
            yield TerminalView(self.model, id="term")
            yield StatusBar("", id="status")

    def on_mount(self) -> None:
        self.set_interval(0.4, self._tick)
        # compose children are not mounted yet — retry until the DOM is ready
        self._bootstrap_attempt()

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
        err = self.serial.open(settings)
        if err is None:
            self.cfg.last = settings
            save_config(self.cfg)
            self._last_rx = time.monotonic()
            self._enter_presses = 0
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
        self._refresh_status()
        return None

    _NO_PORT_MSG = "未连接端口：请按 Ctrl+A P 连接后再试"

    def _remind_connect(self) -> None:
        """Toast shown when a send is attempted with no port connected."""
        self.notify(self._NO_PORT_MSG, severity="warning", timeout=6)

    # ------------------------------------------------------------- HEX send/recv bar
    def _hex_bar(self) -> Horizontal:
        return self.query_one("#hex-bar", Horizontal)

    def _sync_hex_ui(self) -> None:
        """Mount the hex input row only while HEX mode is on; remove it when off.
        (A permanently hidden focusable row used to steal focus and break
        Ctrl+A / other combos in the normal mode.)"""
        present = len(self.query("#hex-bar")) > 0
        if self.cfg.hex_mode:
            if not present:
                self.query_one("#term-root").mount(_HexBar(id="hex-bar"), before="#status")
        elif present:
            with contextlib.suppress(Exception):
                self.query_one("#hex-bar", Horizontal).remove()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "hex-send":
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
        field.text = ""
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
        self._write_capture(data)
        self._last_rx = time.monotonic()
        if self.cfg.hex_mode:
            # 16 进制接收：把收到的字节以十六进制文本送入终端显示。
            # 若光标正停在行中（前面已有内容），先补一个空格，避免上一个
            # 数据块末尾字节与下一个数据块首字节粘在一起（如 "42"+"0D"）。
            text = format_hex(data)
            if text:
                if self.model.mid_line():
                    text = " " + text
                self.model.feed_bytes(text.encode("ascii", "replace"))
        else:
            self.model.feed_bytes(data)
        self._view().mark_dirty()

    # ==================================================================== key handling
    async def _on_key(self, event: Key) -> None:
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
        self.model.clear()
        self._view().mark_dirty()

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
            right = "Ctrl+A Z 菜单"
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
            "\n"
            "-s/-f 内容支持 \\n \\r \\t \\xHH 等转义；-e 支持小数秒。"
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

    args = parser.parse_args(argv)
    if (args.send is not None or args.script is not None) and not args.port:
        parser.error("-s/--send、-f/--script 需要先通过 -p/--port 指定端口")
    if args.exit_idle is not None and args.exit_idle <= 0:
        parser.error("-e/--exit-idle 必须为正数")
    return args


def main(argv=None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    cfg = load_config()
    if args.hex:
        cfg.hex_mode = True  # 本次启动自动开启 16 进制接收/发送

    cli_conn: ConnectionSettings | None = None
    if args.port or args.baud:
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
        cli_conn = conn

    app = PyTermApp(
        cfg=cfg,
        cli_conn=cli_conn,
        exit_idle=args.exit_idle,
        startup_text=args.send,
        startup_script=args.script,
    )
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
