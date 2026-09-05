"""Serial-port layer.

A background reader thread pumps incoming bytes to a *sink* callback (which is
invoked from the reader thread).  The application routes those bytes either to
the terminal display or into a thread-safe queue used by an active YMODEM
transfer.  Writes are protected by a lock so the UI thread and a transfer
worker thread can both transmit safely.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable

from pycom.config import ConnectionSettings

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - serial is a hard dependency
    serial = None  # type: ignore[assignment]
    list_ports = None  # type: ignore[assignment]


def available_ports() -> list[tuple[str, str]]:
    """Return [(device, description), ...] for every detected serial port."""
    if list_ports is None:
        return []
    out: list[tuple[str, str]] = []
    for info in list_ports.comports():
        desc = info.description or ""
        # keep lines short: drop the trailing "(COMn)" pyserial appends on Windows
        if info.device and desc != info.device and " - " in desc:
            desc = desc.split(" - ")[0]
        out.append((info.device, desc))
    return out


class SerialManager:
    """Owns one pyserial port + a background read pump."""

    def __init__(
        self,
        on_data: Callable[[bytes], None],
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.on_data = on_data
        self.on_error = on_error
        self.settings: ConnectionSettings | None = None
        self._ser: serial.Serial | None = None
        self._write_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- state --------------------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    @property
    def port_name(self) -> str:
        return self.settings.port if self.settings else ""

    # -- lifecycle ------------------------------------------------------------------------
    def open(self, settings: ConnectionSettings) -> str | None:
        """Open the port.  Returns an error string on failure, else ``None``."""
        if serial is None:
            return "pyserial is not installed"
        self.close()
        if not settings.port:
            return "未选择端口"
        try:
            ser = serial.Serial(**settings.to_serial_kwargs(timeout=0.05))
        except serial.SerialException as exc:
            return str(exc)
        except (OSError, ValueError) as exc:
            return str(exc)
        self._ser = ser
        self.settings = settings
        self._stop.clear()
        with contextlib.suppress(Exception):
            ser.dtr = settings.dtr
            ser.rts = settings.rts
        self._thread = threading.Thread(target=self._pump, name="pycom-serial", daemon=True)
        self._thread.start()
        return None

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        self._thread = None
        if self._ser is not None:
            with contextlib.suppress(Exception):
                self._ser.cancel_read()
            with contextlib.suppress(Exception):
                self._ser.close()
        self._ser = None

    # -- io ----------------------------------------------------------------------------------
    def write(self, data: bytes) -> bool:
        ser = self._ser
        if ser is None or not ser.is_open or not data:
            return False
        try:
            with self._write_lock:
                ser.write(data)
                ser.flush()
            return True
        except Exception:
            return False

    def set_dtr(self, value: bool) -> None:
        if self._ser is not None:
            with contextlib.suppress(Exception):
                self._ser.dtr = value

    def set_rts(self, value: bool) -> None:
        if self._ser is not None:
            with contextlib.suppress(Exception):
                self._ser.rts = value

    # -- reader thread ---------------------------------------------------------------------------
    def _pump(self) -> None:
        ser = self._ser
        assert ser is not None
        while not self._stop.is_set():
            try:
                if not ser.is_open:
                    break
                waiting = ser.in_waiting
                data = ser.read(waiting if waiting else 1)
            except Exception as exc:  # device unplugged, closed, ...
                if not self._stop.is_set() and self.on_error is not None:
                    self.on_error(f"串口错误: {exc}")
                break
            if data and self.on_data is not None:
                self.on_data(data)
