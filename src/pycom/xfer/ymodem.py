"""YMODEM (XMODEM-1K + batch) protocol engine, pure Python.

Designed for embedded-firmware flashing robustness:
- CRC-16-CCITT (poly 0x1021, init 0x0000) with optional 8-bit checksum fallback
- 128 / 1024 byte blocks (SOH / STX), sequence + complement validation
- configurable timeouts & retries, duplicate-block tolerance, CAN-CAN abort
- works over any byte stream exposing ``read(timeout) -> bytes | None`` and
  ``write(bytes)``, so it is fully unit-testable without a serial port.

Reference: XMODEM/YMODEM spec (Ward Christensen / Chuck Forsberg) and lrzsz.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time
from collections.abc import Callable

SOH = 0x01
STX = 0x02
EOT = 0x04
ACK = 0x06
NAK = 0x15
CAN = 0x18
SUB = 0x1A
C_CHR = 0x43  # 'C'

_ABORT = object()  # sentinel returned by _wait_for on CAN-CAN

# Progress callback: cb(phase, filename, sent, total)
ProgressCB = Callable[[str, str, int, int | None], None]


def crc16(data: bytes, crc: int = 0) -> int:
    """CRC-16-CCITT (MSB first, poly 0x1021, init 0x0000)."""
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def _checksum(data: bytes) -> int:
    return sum(data) & 0xFF


# --------------------------------------------------------------------------- frames --
def build_block(seq: int, data: bytes, crc_mode: bool = True) -> bytes:
    """Wrap ``data`` (128 or 1024 bytes) into a full XMODEM frame."""
    assert len(data) in (128, 1024)
    header = SOH if len(data) == 128 else STX
    frame = bytes([header, seq & 0xFF, (~seq) & 0xFF]) + data
    if crc_mode:
        crc = crc16(data)
        frame += bytes([(crc >> 8) & 0xFF, crc & 0xFF])
    else:
        frame += bytes([_checksum(data)])
    return frame


def build_block0(
    filename: str,
    size: int | None = None,
    mtime: int | None = None,
    mode: int | None = None,
) -> bytes:
    """Build the YMODEM file-header block (SOH, seq 0, 128 bytes)."""
    fields = [filename]
    if size is not None:
        fields.append(str(int(size)))
    if mtime is not None:
        fields.append(oct(int(mtime))[2:])
    if mode is not None:
        fields.append(oct(int(mode))[2:])
    header = " ".join(fields).encode("utf-8", "replace")
    data = header + b"\x00" * (128 - len(header))
    return build_block(0, data, crc_mode=True)


def parse_block0(data: bytes) -> tuple[str, int | None]:
    """Parse a block-0 payload -> (filename, size|None). Empty name = batch end."""
    text = data.split(b"\x00", 1)[0]
    fields = text.split()
    name = ""
    size: int | None = None
    if fields:
        name = fields[0].decode("utf-8", "replace")
        if len(fields) >= 2:
            raw = fields[1]
            try:
                size = int(raw)
            except ValueError:
                try:
                    size = int(raw, 8)  # some senders use octal size
                except ValueError:
                    size = None
    return name, size


# ---------------------------------------------------------------------------- engine --
class YModemEngine:
    """YMODEM send/receive state machine.

    Args:
        read: callable ``read(timeout: float) -> bytes | None`` — must return
            at least one byte or ``None`` on timeout.  Called from the thread
            that runs :meth:`send` / :meth:`recv`.
        write: callable ``write(bytes)``.
    """

    def __init__(
        self,
        read,
        write,
        *,
        timeout: float = 10.0,
        retries: int = 10,
        block_size: int = 1024,
        checksum: bool = False,
        cancel: threading.Event | None = None,
        cb: ProgressCB | None = None,
    ) -> None:
        self._read = read
        self._write = write
        self.timeout = max(0.1, float(timeout))
        self.retries = max(1, int(retries))
        self.block_size = 128 if block_size == 128 else 1024
        self.checksum = bool(checksum)
        self.cancel = cancel or threading.Event()
        self.cb = cb or (lambda *_: None)
        self._buf = b""

    # -- low-level io -------------------------------------------------------------
    def _read1(self, timeout: float) -> int | None:
        if not self._buf:
            chunk = self._read(timeout)
            if not chunk:
                return None
            self._buf += chunk
        b = self._buf[0]
        self._buf = self._buf[1:]
        return b

    def _read_exact(self, n: int, timeout: float) -> bytes | None:
        deadline = time.monotonic() + timeout
        out = bytearray()
        while len(out) < n:
            if self.cancel.is_set():
                return None
            if not self._buf:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                chunk = self._read(min(remaining, 0.2))
                if not chunk:
                    continue
                self._buf += chunk
            take = min(n - len(out), len(self._buf))
            out += self._buf[:take]
            self._buf = self._buf[take:]
        return bytes(out)

    def _emit(self, phase: str, filename: str, sent: int, total: int | None) -> None:
        with contextlib.suppress(Exception):
            self.cb(phase, filename, sent, total)

    # -- wait helpers ----------------------------------------------------------------
    def _wait_for(
        self,
        accept: tuple[int, ...],
        timeout: float,
    ) -> int | None:
        """Read until one of ``accept`` arrives; returns _ABORT on CAN-CAN."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.cancel.is_set():
                return None
            b = self._read1(0.2)
            if b is None:
                continue
            if b == CAN:
                self._read1(0.3)  # consume a possible second CAN
                return _ABORT  # type: ignore[return-value]
            if b in accept:
                return b
        return None

    def _send_can(self) -> None:
        self._write(bytes([CAN, CAN]))

    # ============================================================================= SEND
    def send(
        self,
        stream,
        filename: str = "file.bin",
        size: int | None = None,
    ) -> tuple[bool, str]:
        """Send ``stream`` (opened 'rb') to a YMODEM receiver."""
        crc_mode = not self.checksum
        name = os.path.basename(filename.replace("\\", "/")) or "file.bin"
        total = size
        if total is None:
            try:
                pos = stream.tell()
                stream.seek(0, os.SEEK_END)
                total = stream.tell()
                stream.seek(pos)
            except (OSError, ValueError):
                total = None
        self._emit("start", name, 0, total)

        init = C_CHR if crc_mode else NAK
        ready = None
        for _ in range(self.retries):
            self._write(bytes([init]))
            b = self._wait_for((C_CHR, NAK), self.timeout)
            if b is _ABORT:
                return False, "对方中止"
            if b is not None:
                ready = b
                break
        if ready is None:
            return False, "无响应（对方未进入接收状态）"
        crc_mode = ready == C_CHR
        self._emit("发送文件头…", name, 0, total)

        # --- block 0 (header) ---
        header = build_block0(
            name, total if total is not None else size, mtime=int(time.time()), mode=0o100644
        )
        if not self._ack_block(header, crc_mode, first=True):
            return False, "文件头发送失败"
        self._emit("传输中…", name, 0, total)

        # receiver signals data mode with 'C' (crc) — some bootloaders skip this
        sig = self._read1(1.0)
        if sig == CAN:
            self._send_can()
            return False, "对方中止"
        if sig is not None and sig != C_CHR and sig != NAK:
            self._buf = bytes([sig]) + self._buf  # push back unexpected

        # --- data blocks ---
        seq = 1
        sent = 0
        while not self.cancel.is_set():
            chunk = stream.read(self.block_size)
            if not chunk:
                break
            data = chunk.ljust(self.block_size, bytes([SUB]))
            ok, reason = self._send_data_block(seq, data, crc_mode)
            if not ok:
                return False, reason
            seq = (seq + 1) & 0xFF
            if seq == 0:
                seq = 1
            sent += len(chunk)
            self._emit("progress", name, sent, total)

        # --- EOT sequence ---
        for _attempt in range(self.retries + 1):
            if self.cancel.is_set():
                return False, "用户取消"
            self._write(bytes([EOT]))
            b = self._wait_for((ACK, NAK), self.timeout)
            if b is _ABORT:
                return False, "对方中止"
            if b == ACK:
                break
            # NAK or timeout -> resend EOT (receiver expects two EOTs)
        else:
            return False, "EOT 确认失败"
        self._emit("结束中…", name, sent, total)

        # YMODEM batch terminator: an empty block 0 — tolerated if no reply
        self._write(build_block0("", None))
        end = self._read1(2.0)
        if end == CAN:
            return False, "对方中止"
        self._emit("完成", name, sent, total)
        return True, f"已发送 {sent:,} 字节"

    def _ack_block(self, frame: bytes, crc_mode: bool, first: bool = False) -> bool:
        for _ in range(self.retries + 1):
            if self.cancel.is_set():
                return False
            self._write(frame)
            b = self._wait_for((ACK, NAK), self.timeout)
            if b is _ABORT:
                return False
            if b == ACK:
                return True
            # NAK or timeout -> retransmit
        return False

    def _send_data_block(self, seq: int, data: bytes, crc_mode: bool) -> tuple[bool, str]:
        frame = build_block(seq, data, crc_mode)
        for _ in range(self.retries + 1):
            if self.cancel.is_set():
                return False, "用户取消"
            self._write(frame)
            b = self._wait_for((ACK, NAK), self.timeout)
            if b is _ABORT:
                self._send_can()
                return False, "对方中止"
            if b == ACK:
                return True, ""
        self._send_can()
        return False, "数据块重传超限（可尝试 128 字节块）"

    # ============================================================================= RECV
    def recv(self, open_file) -> tuple[bool, str, str | None]:
        """Receive files.

        ``open_file(filename, size)`` must return a writable binary stream or
        ``None`` to refuse (abort).  Returns ``(ok, message, saved_name)``.
        """
        crc_mode = not self.checksum
        init = C_CHR if crc_mode else NAK

        # kick the sender until the header block arrives
        first = True
        while not self.cancel.is_set():
            self._write(bytes([init]))
            if first:
                first = False
            h = self._read1(self.timeout)
            if h is None:
                continue
            if h == CAN:
                return False, "对方中止", None
            if h == SOH:
                break
            # garbage: keep sending init
        else:
            return False, "超时", None

        # ---- block 0 : file header ----
        self._buf = bytes([h]) + self._buf
        kind, seq, payload = self._receive_block(crc_mode)
        if kind != "data" or seq != 0:
            return False, "文件头错误", None
        filename, fsize = parse_block0(payload)
        if not filename:
            return False, "收到空文件头", None
        stream = open_file(filename, fsize)
        if stream is None:
            self._send_can()
            return False, f"拒绝接收 {filename}", None

        self._write(bytes([ACK]))
        self._emit("开始接收…", filename, 0, fsize)
        # request data in the negotiated mode
        self._write(bytes([init]))
        self._emit("接收中…", filename, 0, fsize)

        expected = 1
        last_seq = -1
        sent = 0
        ok = False
        reason = ""
        try:
            while not self.cancel.is_set():
                kind, seq, payload = self._receive_block(crc_mode)
                if kind == "abort":
                    reason = "对方中止"
                    break
                if kind == "eot":
                    # first EOT -> NAK ; second -> ACK (standard YMODEM)
                    self._write(bytes([NAK]))
                    again = self._read1(self.timeout)
                    if again == CAN:
                        reason = "对方中止"
                        break
                    if again == EOT:
                        self._write(bytes([ACK]))
                        ok = True
                    elif again is None:
                        # sender that only uses a single EOT + expects ACK
                        self._write(bytes([ACK]))
                        ok = True
                    break
                if kind == "bad":
                    self._write(bytes([NAK]))
                    continue

                if seq == last_seq:
                    self._write(bytes([ACK]))  # duplicate -> re-ack
                    continue
                if seq != expected:
                    self._write(bytes([NAK]))
                    continue

                stream.write(payload)
                sent += len(payload)
                expected = (expected + 1) & 0xFF
                if expected == 0:
                    expected = 1
                last_seq = seq
                self._write(bytes([ACK]))
                self._emit("progress", filename, sent, fsize)
            # drop the 0x1A padding of the last partial block
            if ok and fsize is not None:
                with contextlib.suppress(Exception):
                    stream.truncate(fsize)
        finally:
            with contextlib.suppress(Exception):
                stream.close()

        if not ok:
            self._send_can()
            return False, reason or "接收失败", filename

        # optional YMODEM batch terminator block
        term = self._read1(1.5)
        if term == SOH:
            self._buf = bytes([term]) + self._buf
            kind, seq, payload = self._receive_block(crc_mode)
            if kind == "data" and seq == 0:
                name2, _ = parse_block0(payload)
                if not name2:  # end-of-batch marker
                    self._write(bytes([ACK]))
        self._emit("完成", filename, sent, fsize)
        return True, f"已接收 {filename} ({sent:,} 字节)", filename

    def _receive_block(self, crc_mode: bool) -> tuple[str, int, bytes]:
        """Read one full block.  Returns (kind, seq, payload).

        kind: 'data' | 'eot' | 'abort' | 'bad'
        """
        h = self._read1(self.timeout)
        if h is None:
            return "bad", -1, b""
        if h == CAN:
            return "abort", -1, b""
        if h == EOT:
            return "eot", -1, b""
        if h == SOH:
            n = 128
        elif h == STX:
            n = 1024
        else:
            return "bad", -1, b""

        seq = self._read1(self.timeout)
        comp = self._read1(self.timeout)
        if seq is None or comp is None or comp != ((~seq) & 0xFF):
            return "bad", -1, b""
        payload = self._read_exact(n, self.timeout)
        if payload is None:
            return "bad", -1, b""
        if crc_mode:
            crc_bytes = self._read_exact(2, self.timeout)
            if crc_bytes is None:
                return "bad", -1, b""
            good = (crc_bytes[0] << 8 | crc_bytes[1]) == crc16(payload)
        else:
            chk = self._read_exact(1, self.timeout)
            if chk is None:
                return "bad", -1, b""
            good = chk[0] == _checksum(payload)
        if not good:
            return "bad", -1, b""
        return "data", seq, payload
