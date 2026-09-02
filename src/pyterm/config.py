"""Configuration data models and JSON persistence.

Kept free of any TUI/serial imports so it is importable everywhere
(tests, protocol layer, packaging).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any

import platformdirs

from pyterm import APP_NAME

BAUDRATES = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600, 1500000]


@dataclass
class ConnectionSettings:
    """Full serial connection parameters (UI-editable)."""

    port: str = ""
    baudrate: int = 115200
    bytesize: int = 8  # 5..8
    parity: str = "N"  # N / E / O / M / S
    stopbits: float = 1.0  # 1 / 1.5 / 2
    flow: str = "none"  # none / rtscts / xonxoff
    dtr: bool = True
    rts: bool = True

    def short(self) -> str:
        sb = int(self.stopbits) if self.stopbits.is_integer() else self.stopbits
        flow = " hw" if self.flow == "rtscts" else (" sw" if self.flow == "xonxoff" else "")
        port = self.port or "(none)"
        return f"{port} {self.baudrate} {self.bytesize}{self.parity}{sb}{flow}"

    def to_serial_kwargs(self, timeout: float) -> dict:
        return {
            "port": self.port,
            "baudrate": self.baudrate,
            "bytesize": self.bytesize,
            "parity": self.parity,
            "stopbits": self.stopbits,
            "rtscts": self.flow == "rtscts",
            "xonxoff": self.flow == "xonxoff",
            "dsrdtr": False,
            "timeout": timeout,
            "write_timeout": 1.0,
        }


@dataclass
class AppConfig:
    """Persisted application options (JSON in the user config dir)."""

    # --- terminal behaviour ---
    local_echo: bool = False
    wrap: bool = True
    enter_sends: str = "cr"  # cr | crlf | lf | none
    rx_add_cr: bool = False  # received LF is displayed as CR+LF
    rx_add_lf: bool = False  # received CR is displayed as CR+LF
    backspace_sends: str = "del"  # del(0x7f) | bs(0x08)
    decode: str = "utf-8"  # utf-8 | gbk | latin-1
    send_vt_sequences: bool = True  # arrows/f-keys -> VT escape sequences

    # --- capture ---
    capture_path: str = ""
    capture_timestamps: bool = False

    # --- file transfer ---
    xfer_timeout: float = 10.0  # seconds waiting for peer
    xfer_retries: int = 10
    xfer_block_size: int = 1024  # 1024 | 128

    # --- remember last serial params ---
    last: ConnectionSettings = field(default_factory=ConnectionSettings)

    # --- ui ---
    auto_scroll: bool = True
    scrollback: int = 4000


def _config_dir() -> str:
    return platformdirs.user_config_dir(APP_NAME, roaming=True)


def config_path() -> str:
    return os.path.join(_config_dir(), "config.json")


def load_config() -> AppConfig:
    cfg = AppConfig()
    try:
        with open(config_path(), encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)
    except (OSError, ValueError):
        return cfg

    def _coerce_conn(raw: Any) -> ConnectionSettings:
        conn = ConnectionSettings()
        if isinstance(raw, dict):
            for key in conn.__dataclass_fields__:  # type: ignore[attr-defined]
                if key in raw and raw[key] is not None:
                    setattr(conn, key, raw[key])
        return conn

    if "last" in data:
        cfg.last = _coerce_conn(data["last"])
    for key in cfg.__dataclass_fields__:  # type: ignore[attr-defined]
        if key != "last" and key in data and data[key] is not None:
            setattr(cfg, key, data[key])
    # sanity: clamp numeric fields
    cfg.last.baudrate = validate_baud(cfg.last.baudrate)
    if cfg.xfer_timeout <= 0:
        cfg.xfer_timeout = 10.0
    if cfg.xfer_retries <= 0:
        cfg.xfer_retries = 10
    return cfg


def save_config(cfg: AppConfig) -> None:
    try:
        os.makedirs(_config_dir(), exist_ok=True)
        with open(config_path(), "w", encoding="utf-8") as fh:
            json.dump(asdict(cfg), fh, indent=2, ensure_ascii=False)
    except OSError:
        pass  # non-fatal


# --- small helpers -------------------------------------------------------------------


def validate_baud(rate: int) -> int:
    return rate if 50 <= rate <= 4_000_000 else 115200


def baudrate_choices() -> list[int]:
    return BAUDRATES
