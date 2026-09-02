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
from typing import Any

from textual.app import App
from textual.containers import Container
from textual.events import Key

from pyterm import APP_NAME, __version__
from pyterm.config import AppConfig, ConnectionSettings, load_config, save_config
from pyterm.keys import KeyMapper
from pyterm.screens.base import ConfirmDialog
from pyterm.screens.connection import ConnectionScreen
from pyterm.screens.help import MainMenuScreen
from pyterm.screens.options import OptionsScreen
from pyterm.screens.transfer import RecvScreen, SendScreen
from pyterm.serialio import SerialManager
from pyterm.termdisplay.view import StatusBar, TerminalView
from pyterm.termdisplay.vt import TerminalModel
from pyterm.xfer.ymodem import YModemEngine

_PREFIX_FUNCS = {
    "z": "主菜单",
    "x": "退出",
    "s": "发送文件",
    "r": "接收文件",
    "c": "清屏",
    "l": "捕获开/关",
    "p": "串口参数",
    "o": "选项",
    "a": "自动回绕",
    "e": "本地回显",
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
    SUB_TITLE = "串口终端 · YMODEM"

    def __init__(
        self,
        cfg: AppConfig | None = None,
        cli_conn: ConnectionSettings | None = None,
    ) -> None:
        super().__init__()
        self.cfg = cfg or AppConfig()
        self.cli_conn = cli_conn

        self.model = TerminalModel(80, 24, scrollback=self.cfg.scrollback, decode=self.cfg.decode)
        self.model.rx_add_cr = self.cfg.rx_add_cr
        self.model.rx_add_lf = self.cfg.rx_add_lf

        self.serial = SerialManager(on_data=self._on_rx, on_error=self._on_serial_error)
        self.mapper = KeyMapper(self.cfg)

        self._prefix = False
        self._tx = 0
        self._rx = 0

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
        self._refresh_status()

    # ======================================================================= helpers
    def _view(self) -> TerminalView:
        return self.query_one("#term", TerminalView)

    def _status(self) -> StatusBar:
        return self.query_one("#status", StatusBar)

    # =================================================================== serial io
    def is_connected(self) -> bool:
        return self.serial.is_open

    def open_serial(self, settings: ConnectionSettings) -> str | None:
        if self._xfer_thread is not None and self._xfer_thread.is_alive():
            return "请先完成/取消进行中的文件传输"
        err = self.serial.open(settings)
        if err is None:
            self.cfg.last = settings
            save_config(self.cfg)
            self._refresh_status()
        return err

    def close_serial(self) -> None:
        self.cancel_transfer()
        self.serial.close()
        self._stop_capture()
        self._refresh_status()

    def send_bytes(self, data: bytes) -> bool:
        if not data:
            return False
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
        data = self.mapper.map(event.key, event.character)
        if data is not None:
            event.stop()
            self.send_bytes(data)

    # -- Ctrl+A prefix model --------------------------------------------------------------
    def _enter_prefix(self) -> None:
        self._prefix = True
        self._status().update(
            "Ctrl+A 前缀已按下 —— 再按功能键 (Z 帮助 S 发送 R 接收 X 退出 …) · 再次 Ctrl+A=发送 0x01 · Esc=取消"
        )

    def _cancel_prefix(self) -> None:
        self._prefix = False
        self._refresh_status()

    def _prefix_second(self, event: Key) -> None:
        self._prefix = False
        if event.key == "ctrl+a":
            self.send_bytes(b"\x01")
        elif event.key == "escape":
            pass
        else:
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
                self.notify("请先连接串口（Ctrl+A P）", severity="warning")
            else:
                self.push_screen(SendScreen())
        elif code == "r":
            if not self.is_connected():
                self.notify("请先连接串口（Ctrl+A P）", severity="warning")
            else:
                self.push_screen(RecvScreen())
        elif code == "c":
            self.clear_terminal()
        elif code == "l":
            self.toggle_capture()
        elif code == "p":
            self.push_screen(ConnectionScreen())
        elif code == "o":
            self.push_screen(OptionsScreen())
        elif code == "a":
            self.cfg.wrap = not self.cfg.wrap
            self.apply_config()
            self._refresh_status()
        elif code == "e":
            self.cfg.local_echo = not self.cfg.local_echo
            save_config(self.cfg)
            self._refresh_status()

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
        self._refresh_status()

    # ============================================================================ status
    def _status_text(self) -> str:
        conn = self.cfg.last.short()
        state = "已连接" if self.is_connected() else "未连接"
        flags = []
        if self.is_connected():
            flags.append(f"TX {self._tx:,}")
            flags.append(f"RX {self._rx:,}")
        if self._capture_fh is not None:
            flags.append("●捕获")
        if self.cfg.local_echo:
            flags.append("回显")
        if self.cfg.wrap:
            flags.append("回绕")
        right = "Ctrl+A Z 菜单" if not self._prefix else ""
        mid = "  ".join(flags)
        return f" {conn} | {state} | {mid}".rstrip(" |") + (f"    {right}" if right else "")

    def _refresh_status(self) -> None:
        with contextlib.suppress(Exception):  # DOM not ready / already torn down
            self._status().update(self._status_text())

    def _tick(self) -> None:
        if self._prefix:
            return
        self._refresh_status()

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
        description="minicom 风格跨平台串口终端，内置 YMODEM 文件传输",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-p", "--port", default=None, help="串口，如 COM3 或 /dev/ttyUSB0")
    parser.add_argument("-b", "--baud", type=int, default=None, help="波特率")
    parser.add_argument("-d", "--data-bits", type=int, default=None, help="数据位 (5-8)")
    parser.add_argument("--parity", default=None, help="校验 N/E/O")
    parser.add_argument("-s", "--stop-bits", type=float, default=None, help="停止位 1/1.5/2")
    parser.add_argument("-f", "--flow", default=None, help="流控 none/rtscts/xonxoff")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    cfg = load_config()

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

    app = PyTermApp(cfg=cfg, cli_conn=cli_conn)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
