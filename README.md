<div align="center">

# PyCom

**A minicom-style, cross-platform serial terminal TUI with robust YMODEM file transfer.**

Built for embedded firmware flashing (STM32 &amp; other ymodem bootloaders) and everyday serial debugging.

[简体中文](README.zh-CN.md) · [English](#)

</div>

<p align="center">
  <a href="https://github.com/greyskysoul/pycom/actions/workflows/ci.yml"><img src="https://github.com/greyskysoul/pycom/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/pycom/"><img src="https://img.shields.io/pypi/v/pycom.svg" alt="PyPI version"></a>
  <a href="https://pypi.org/project/pycom/"><img src="https://img.shields.io/pypi/pyversions/pycom.svg" alt="Python versions"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <a href="https://github.com/greyskysoul/pycom/releases"><img src="https://img.shields.io/github/v/release/greyskysoul/pycom" alt="GitHub release"></a>
  <a href="https://github.com/greyskysoul/pycom/stargazers"><img src="https://img.shields.io/github/stars/greyskysoul/pycom?style=social" alt="GitHub stars"></a>
</p>

## Features

- **Full-screen terminal UI** — device ANSI/VT output is rendered correctly, with scrollable history.
- **Ctrl+A prefix-key + overlay menu** — familiar minicom interaction model.
- **YMODEM send / receive** — CRC-16-CCITT, configurable 128/1024-byte blocks, timeout retransmission, progress display, cancellable.
- **Live port / baudrate / parity editing** with persistent configuration.
- Session capture (logging), local echo, line-ending conversion, HEX display, and more.
- Overlays (menu / connection / options / confirm dialogs) auto-adapt to small windows (compact full-screen layout).
- When the window is too small to be usable (< 20 columns or < 5 rows), prints a hint and exits cleanly.
- `--bare` headless serial pass-through: stdin → serial port, serial RX → stdout, for driving by external processes such as AI agents.
- Windows (Windows Terminal recommended) and Linux.

## Installation

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux
source .venv/bin/activate
pip install -e ".[dev]"
```

Or install from PyPI:

```bash
pip install pycom
```

## Usage

```bash
pycom                          # start, then press Ctrl+A Z for the menu / auto connection dialog
pycom --port COM3 --baud 115200
pycom /dev/ttyUSB0 -b 921600
# send a string right after connecting (supports \n \r \t \xHH escapes)
pycom -p COM3 -s "AT\r"
# send a script file line by line (# starts a comment line)
pycom -p COM3 -f boot.txt
# exit automatically after 5 seconds without receiving any byte (-e supports fractional seconds)
pycom -p COM3 -s "AT\r" -e 5
# start with 16-hex receive/send mode (HEX) enabled
pycom -p COM3 --hex
# headless pure serial pass-through (--bare): hides all UI, requires a port.
# stdin bytes → serial, serial RX → stdout; hand the terminal to an AI agent, etc.:
pycom --bare -p COM3 -b 115200
```

> The `-d/-s/-f` shorthands are gone: use the full names `--data-bits`/`--stop-bits`/`--flow`
> for data bits / stop bits / flow control. `-s`/`-f` now mean "send string/script after
> connecting" and require `-p/--port`.
>
> `--bare` is a UI-less pure pass-through mode: it only uses the connection parameters
> (`-p/-b/--parity/...`) and cannot be combined with `-s/-f/-e/--hex`.

## Key bindings (Ctrl+A prefix)

| Keys | Action |
| ---------- | ------------------------------- |
| `Ctrl+A` `Z` | Open main menu / help |
| `Ctrl+A` `X` | Quit |
| `Ctrl+A` `S` | Send file (YMODEM) |
| `Ctrl+A` `R` | Receive file (YMODEM) |
| `Ctrl+A` `L` | Toggle session capture |
| `Ctrl+A` `C` | Clear screen |
| `Ctrl+A` `P` | Serial parameters (connection) |
| `Ctrl+A` `O` | Options |
| `Ctrl+A` `H` | Toggle 16-hex receive/send (HEX) |
| `Esc` (in prefix) | Cancel prefix |

> Local echo, auto-wrap, etc. live in the `Ctrl+A` `O` options overlay (off by default)
> rather than occupying prefix shortcuts.
>
> **HEX mode** (`Ctrl+A H` or the options page, persistent): received bytes are shown as
> hex text; a multi-line hex input area appears at the bottom (only valid characters, auto
> space-separated per byte, wrapping at 4/8/16 bytes per line depending on window width).
> Keys no longer send directly — click the bottom "Send" button to parse the input as
> bytes. Toggling via shortcut auto-focuses the input area.
>
> **Virtual loopback device** (debugging): start with `--enable-debug`, then `LOOPBACK`
> appears at the end of the port list in `Ctrl+A P`. No real port needed — every byte sent
> is echoed back (pure loopback), ideal for testing TX/RX and HEX display without hardware.

## Development

```bash
pytest            # unit tests: CRC / frames / YMODEM loopback, etc.
ruff check .      # static checks
mypy src/pycom    # type checks
```

Layout: `src/pycom/` (`serialio.py` serial I/O, `termdisplay/` terminal rendering,
`xfer/ymodem.py` protocol engine, `screens/` the individual screens, `keys.py` key state
machine, `app.py` main program).

## Packaging

```bash
pip install pyinstaller
pyinstaller packaging/pycom.spec    # produces the onedir layout dist/pycom/ (size-optimized)
```

Size optimizations (already in `packaging/pycom.spec`):

- **onedir layout**: avoids onefile's per-launch self-extraction overhead, easier to inspect/remove unused runtime files on embedded devices
- **exclude ssl/network modules**: a serial terminal doesn't need SSL — saves ~6MB (libcrypto/libssl)
- **exclude unused Textual widgets** and stdlib extension modules (`_decimal`/`_lzma`/`_bz2`/`_zstd`, etc.)
- **strip + UPX**: effective on Linux and Python 3.12; Windows + Python 3.14 auto-skips UPX due to CFG-enabled binaries

CI (`.github/workflows/ci.yml`) runs lint/type/tests and builds artifacts on Windows and
Ubuntu (CI installs UPX automatically). Pushing a `v*` tag (e.g. `v0.1.0`) automatically
creates a GitHub Release from the build artifacts.

## Tech stack

- **Python ≥3.11**, `src` layout, stdlib-first
- **Textual** — terminal TUI framework (modal overlays / forms / file tree, native overlay menu support)
- **pyserial** — serial port (including `list_ports` enumeration)
- **pyte** — VT terminal emulation for the device RX byte stream (subclassing its `Screen` to capture scrolled-out content for history; LGPLv3)
- **In-house YMODEM engine** (`xfer/ymodem.py`): CRC-16-CCITT, SOH/STX, 128/1024 blocks,
  configurable timeout/retry, duplicate-block tolerance, CAN-CAN abort, auto-retransmit on bad block, progress callbacks
- Packaging: PyInstaller; testing: pytest (including Textual Pilot headless UI tests), ruff, mypy

## Directory layout

```txt
src/pycom/
  app.py              main program (Ctrl+A prefix state machine, serial routing, transfer worker, capture)
  config.py           config data models + JSON persistence
  serialio.py         serial layer (background read thread, write lock, port enumeration, hot-plug)
  keys.py             key → byte mapping (line ending / backspace / arrow VT sequences…)
  termdisplay/
    vt.py             pyte terminal model (decode, scroll history, resize)
    view.py           TerminalView / StatusBar widgets
  xfer/ymodem.py      YMODEM bidirectional protocol engine (pure Python, unit-testable without serial)
  screens/            connection, main menu, options, file/dir picker, transfer screens
  resources/app.tcss  theme
tests/unit/           CRC/frame/block0, engine loopback (incl. error injection), keys, terminal model, Pilot UI
packaging/            PyInstaller launcher and spec
```

## Configuration

The config file is JSON (`%APPDATA%\pycom\config.json` on Windows /
`~/.config/pycom/config.json` on Linux); edit it via Ctrl+A O and it saves during runtime.

## Known scope (Roadmap)

- v1 included: YMODEM bidirectional transfer, capture log, line-ending/echo/decode/flow config, scrollback, HEX rendering
- v1 not included: XMODEM/ZMODEM/Kermit, ASCII send, macro scripts, dialing directory, split-pane multi-session
- Recommended to run under **Windows Terminal** (full ConPTY/color support)

## Interop testing

1. Cross-validate with lrzsz on Linux: `sz -Y file` to PyCom receive; `rz -Y` to PyCom send
2. Use socat(pty)/com0com virtual serial ports for end-to-end loopback on Windows/Linux
3. STM32 bootloader flashing on hardware: verify at 115200/921600 each with a large file (SHA256 compare)

## AI Disclosure

This project used an AI programming assistant (GitHub Copilot) during development to help
write, review, and debug code. All code was manually reviewed and verified by automated
tests (pytest / ruff / mypy).
