"""Unit tests for config load/save (in a temp dir)."""

from __future__ import annotations

import pytest

import pyterm.config as cfgmod
from pyterm.config import AppConfig, ConnectionSettings


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "_config_dir", lambda: str(tmp_path))
    return tmp_path


def test_save_load_roundtrip(tmp_config_dir):
    cfg = AppConfig()
    cfg.local_echo = True
    cfg.enter_sends = "crlf"
    cfg.decode = "gbk"
    cfg.last = ConnectionSettings(port="COM9", baudrate=921600, flow="rtscts")
    cfgmod.save_config(cfg)

    loaded = cfgmod.load_config()
    assert loaded.local_echo is True
    assert loaded.enter_sends == "crlf"
    assert loaded.decode == "gbk"
    assert loaded.last.port == "COM9"
    assert loaded.last.baudrate == 921600
    assert loaded.last.flow == "rtscts"


def test_load_missing_returns_defaults(tmp_config_dir):
    cfg = cfgmod.load_config()
    assert isinstance(cfg, AppConfig)
    assert cfg.last.port == ""


def test_load_corrupt_returns_defaults(tmp_config_dir):
    (tmp_config_dir / "config.json").write_text("{ not json !", encoding="utf-8")
    cfg = cfgmod.load_config()
    assert isinstance(cfg, AppConfig)
