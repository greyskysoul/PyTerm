"""Unit tests for the YMODEM protocol engine (crc, frames, full round-trips)."""

from __future__ import annotations

import io
import queue
import threading

from pyterm.xfer.ymodem import (
    SOH,
    STX,
    YModemEngine,
    build_block,
    build_block0,
    crc16,
    parse_block0,
)


def test_crc16_xmodem_vector():
    # XMODEM CRC (poly 0x1021, init 0x0000, no reflection) of "123456789" == 0x31C3
    assert crc16(b"123456789") == 0x31C3


def test_build_block_128():
    data = b"A" * 128
    frame = build_block(1, data)
    assert frame[0] == SOH
    assert frame[1] == 1
    assert frame[2] == 0xFE  # complement
    assert len(frame) == 128 + 3 + 2
    assert (frame[-2] << 8 | frame[-1]) == crc16(data)


def test_build_block_1024():
    data = bytes(range(256)) * 4  # 1024 bytes
    frame = build_block(7, data)
    assert frame[0] == STX
    assert frame[1] == 7
    assert frame[2] == 0xF8
    assert len(frame) == 1024 + 3 + 2


def test_block0_parse():
    frame = build_block0("firmware.bin", 123456)
    payload = frame[3:-2]
    name, size = parse_block0(payload)
    assert frame[1] == 0
    assert name == "firmware.bin"
    assert size == 123456


def test_block0_empty_is_batch_end():
    frame = build_block0("", None)
    name, size = parse_block0(frame[3:-2])
    assert name == ""
    assert size is None


# --------------------------------------------------------------------------- helpers


class _Link:
    """In-memory half-duplex pipe: ``write`` puts into the peer's queue."""

    def __init__(self, peer: _Link | None = None, split: int = 0) -> None:
        self.peer = peer
        self.q: queue.Queue[bytes] = queue.Queue()
        self.split = split  # if >0, enqueue in small chunks (streaming realism)

    def read(self, timeout: float) -> bytes | None:
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def write(self, data: bytes) -> None:
        assert self.peer is not None
        if self.split and len(data) > self.split:
            for i in range(0, len(data), self.split):
                self.peer.q.put(data[i : i + self.split])
        else:
            self.peer.q.put(data)


class _CorruptOnceLink(_Link):
    """Sender-side link that corrupts the CRC of the FIRST STX data block."""

    def write(self, data: bytes) -> None:
        if not getattr(self, "_done", False) and len(data) >= 1029 and data[0] == STX:
            bad = bytearray(data)
            bad[-1] ^= 0xFF  # flip one CRC byte
            self._done = True
            super().write(bytes(bad))
            return
        super().write(data)


class _SnapIO(io.BytesIO):
    """BytesIO that keeps a copy of its content once the engine closes it."""

    def __init__(self) -> None:
        super().__init__()
        self.snapshot: bytes | None = None

    def close(self) -> None:
        self.snapshot = self.getvalue()
        super().close()


def _roundtrip(payload: bytes, split: int = 0, sender_link_cls=_Link) -> tuple[bool, str, dict]:
    received: dict = {}

    def open_file(name, size):
        buf = _SnapIO()
        received["name"] = name
        received["size"] = size
        received["buf"] = buf
        return buf

    chan_a = sender_link_cls(split=split)  # sender side
    chan_b = _Link(split=split)  # receiver side
    chan_a.peer = chan_b
    chan_b.peer = chan_a

    recv_eng = YModemEngine(chan_b.read, chan_b.write, timeout=0.3, retries=6, block_size=1024)
    send_eng = YModemEngine(chan_a.read, chan_a.write, timeout=0.3, retries=6, block_size=1024)
    errors: list[Exception] = []

    def do_recv() -> None:
        try:
            received["res"] = recv_eng.recv(open_file)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    t = threading.Thread(target=do_recv, daemon=True)
    t.start()
    ok, msg = send_eng.send(io.BytesIO(payload), filename="test.bin", size=len(payload))
    t.join(timeout=15)
    if errors:  # pragma: no cover
        raise errors[0]
    return ok, msg, received


# ----------------------------------------------------------------------------- tests


def test_roundtrip_small_file():
    payload = b"hello ymodem world\n" * 40  # ~840 bytes -> partial 1024 block
    ok, msg, recv = _roundtrip(payload, split=7)
    assert ok, msg
    assert recv["name"] == "test.bin"
    assert recv["buf"].snapshot == payload


def test_roundtrip_multi_block_exact():
    payload = bytes(range(256)) * 16  # exactly 4096 bytes (4 blocks)
    ok, msg, recv = _roundtrip(payload, split=3)
    assert ok, msg
    assert recv["buf"].snapshot == payload


def test_roundtrip_recovers_after_corruption():
    # corrupt the first data block -> receiver NAKs -> sender retransmits
    payload = b"\x5a" * 5000  # 5 blocks
    ok, msg, recv = _roundtrip(payload, split=11, sender_link_cls=_CorruptOnceLink)
    assert ok, msg
    assert recv["buf"].snapshot == payload
