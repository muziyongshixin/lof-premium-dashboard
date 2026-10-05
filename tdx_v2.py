"""Minimal client for the current TDX 7709 daily-kline protocol.

Adapted from jiangtaovan/tdxrs pull request #14 at commit
30b0d9691a6ccbb698268fe1d6d448bbe6ab9d4c (MIT). The legacy 0c02 request
family started returning empty market-data payloads in 2026; current TDX quote
servers use the 000a request frame implemented here.
License: licenses/tdxrs-MIT.txt. Volume is fund units (份), amount is CNY.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass


MAGIC_RESPONSE = b"\xb1\xcb\x74\x00"
HEADER_LENGTH = 16
KLINE_DAILY = 4
MAX_BARS_PER_REQUEST = 700

TDX_V2_SERVERS = (
    ("行情主站1", "121.36.248.138", 7709),
    ("行情主站2", "123.60.47.136", 7709),
    ("行情主站3", "121.37.207.165", 7709),
)

_LOGIN_FRAME = bytes.fromhex(
    "000300010001460046000f1204002d3100000000000000000027100e00000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "00000000000000000000000000000000"
)
_BARS_FRAME = bytes.fromhex(
    "000a00681301300030002e120000433044453030000000000000000000000000"
    "000000000400010000000000bc02000001010001000000000000"
)
_BARS_CODE_OFFSET = 14
_BARS_MARKET_OFFSET = 12
_BARS_CATEGORY_OFFSET = 36
_BARS_START_OFFSET = 40
_BARS_COUNT_OFFSET = 44


class TdxV2Error(Exception):
    pass


@dataclass
class TdxV2Bar:
    date: int
    open: float
    high: float
    low: float
    close: float
    amount: float
    volume: float


class TdxV2Client:
    def __init__(self, host: str, port: int = 7709, timeout: float = 6.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._socket: socket.socket | None = None

    def connect(self) -> None:
        self._socket = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._send(_LOGIN_FRAME)
        self._receive_payload()

    def disconnect(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None

    def _send(self, payload: bytes) -> None:
        if self._socket is None:
            raise TdxV2Error("client is not connected")
        self._socket.sendall(payload)

    def _receive_exactly(self, length: int) -> bytes:
        if self._socket is None:
            raise TdxV2Error("client is not connected")
        data = bytearray()
        while len(data) < length:
            chunk = self._socket.recv(length - len(data))
            if not chunk:
                raise TdxV2Error("connection closed before response completed")
            data.extend(chunk)
        return bytes(data)

    def _receive_payload(self) -> bytes:
        if self._socket is None:
            raise TdxV2Error("client is not connected")
        self._socket.settimeout(self.timeout)
        header = self._receive_exactly(HEADER_LENGTH)
        if not header.startswith(MAGIC_RESPONSE):
            raise TdxV2Error(f"unexpected response magic: {header[:4].hex()}")
        payload_length = struct.unpack("<H", header[12:14])[0]
        echoed_length = struct.unpack("<H", header[14:16])[0]
        if payload_length != echoed_length:
            raise TdxV2Error(f"response length mismatch: {payload_length} != {echoed_length}")
        return self._receive_exactly(payload_length)

    def get_daily_bars(
        self,
        market: int,
        code: str,
        count: int = 320,
        start: int = 0,
    ) -> list[TdxV2Bar]:
        if count < 1 or count > MAX_BARS_PER_REQUEST:
            raise ValueError(f"count must be between 1 and {MAX_BARS_PER_REQUEST}")
        if len(code) != 6 or not code.isdigit():
            raise ValueError(f"invalid TDX symbol: {code}")

        frame = bytearray(_BARS_FRAME)
        frame[_BARS_MARKET_OFFSET] = market
        frame[_BARS_CODE_OFFSET : _BARS_CODE_OFFSET + 6] = code.encode("ascii")
        frame[_BARS_CATEGORY_OFFSET : _BARS_CATEGORY_OFFSET + 2] = struct.pack("<H", KLINE_DAILY)
        frame[_BARS_START_OFFSET : _BARS_START_OFFSET + 4] = struct.pack("<I", start)
        frame[_BARS_COUNT_OFFSET : _BARS_COUNT_OFFSET + 2] = struct.pack("<H", count)
        self._send(bytes(frame))
        return self._decode_bars(self._receive_payload(), code)[-count:]

    @staticmethod
    def _decode_bars(payload: bytes, code: str) -> list[TdxV2Bar]:
        if len(payload) < 153:
            raise TdxV2Error(f"daily-kline payload too short: {len(payload)}")
        if payload[2:8] != code.encode("ascii"):
            raise TdxV2Error(f"response symbol mismatch: {payload[2:8]!r} != {code}")
        if (len(payload) - 153) % 36:
            raise TdxV2Error("truncated daily-kline record")

        bars: list[TdxV2Bar] = []
        position = 33
        records_end = len(payload) - 120
        while position + 36 <= records_end:
            record = payload[position : position + 36]
            day, _time = struct.unpack("<II", record[:8])
            open_price, high, low, close, amount, volume = struct.unpack("<6f", record[8:32])
            bars.append(
                TdxV2Bar(
                    date=int(day),
                    open=float(open_price),
                    high=float(high),
                    low=float(low),
                    close=float(close),
                    amount=float(amount),
                    volume=float(volume),
                )
            )
            position += 36
        return bars
