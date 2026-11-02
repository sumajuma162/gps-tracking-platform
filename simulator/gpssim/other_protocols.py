"""GT06 (TCP) and generic JSON-over-HTTP tracker clients (spec A16: the simulator speaks every supported protocol
over the network). They take the same ``AvlRecordSpec`` stream as the Teltonika clients, so every scenario runs
with every protocol."""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from gpsgw.protocols.gt06.decoder import encode_packet
from gpsgw.protocols.teltonika.encoder import AvlRecordSpec

from gpssim.client import Rejected

IGNITION, ODOMETER = 239, 16


def _ignition(rec: AvlRecordSpec) -> int:
    return int(rec.io.get(IGNITION, (1, 0))[1])


def gt06_location(rec: AvlRecordSpec, serial: int) -> bytes:
    """A GT06 0x22 location packet (with ACC and mileage) for one simulated record."""
    t = rec.time
    flags = (rec.heading % 360) | (1 << 12 if rec.satellites else 0) | (1 << 10 if rec.latitude >= 0 else 0) \
        | (1 << 11 if rec.longitude < 0 else 0)
    mileage = int(rec.io.get(ODOMETER, (4, 0))[1])
    content = (bytes([t.year - 2000, t.month, t.day, t.hour, t.minute, t.second, 0xC0 | min(rec.satellites, 15)])
               + round(abs(rec.latitude) * 1_800_000).to_bytes(4, "big")
               + round(abs(rec.longitude) * 1_800_000).to_bytes(4, "big")
               + bytes([min(max(rec.speed_kmh, 0), 255)]) + flags.to_bytes(2, "big")
               + (640).to_bytes(2, "big") + bytes([4]) + (0x1F40).to_bytes(2, "big") + (0x00C351).to_bytes(3, "big")
               + bytes([_ignition(rec), 0, 0]) + mileage.to_bytes(4, "big"))
    return encode_packet(0x22, content, serial)


@dataclass
class Gt06Client:
    host: str
    port: int
    imei: str
    timeout: float = 10.0

    def __post_init__(self) -> None:
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.serial = 1

    @property
    def connected(self) -> bool:
        return self.writer is not None and not self.writer.is_closing()

    def _next(self) -> int:
        self.serial = (self.serial + 1) & 0xFFFF
        return self.serial

    async def connect(self) -> None:
        self.reader, self.writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), self.timeout)
        serial = self._next()
        self.writer.write(encode_packet(0x01, bytes.fromhex(self.imei.rjust(16, "0")), serial))
        await self.writer.drain()
        try:
            reply = await asyncio.wait_for(self.reader.readexactly(10), self.timeout)
        except asyncio.IncompleteReadError as exc:     # unregistered: the server closes without an ACK
            await self.close()
            raise Rejected(self.imei) from exc
        if reply != encode_packet(0x01, b"", serial):
            await self.close()
            raise Rejected(self.imei)

    async def send(self, records: list[AvlRecordSpec]) -> int:
        """Locations have no ACK in GT06; a heartbeat after them is ACKed only once everything before it was
        stored, so its ACK confirms the whole batch."""
        assert self.reader is not None and self.writer is not None
        for rec in records:
            self.writer.write(gt06_location(rec, self._next()))
        serial = self._next()
        info = 0b0100_0000 | (_ignition(records[-1]) << 1) if records else 0
        self.writer.write(encode_packet(0x13, bytes([info, 4, 4, 0, 1]), serial))
        await self.writer.drain()
        ack = await asyncio.wait_for(self.reader.readexactly(10), self.timeout)
        if ack != encode_packet(0x13, b"", serial):
            raise ConnectionError("unexpected heartbeat reply")
        return len(records)

    async def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except (ConnectionError, OSError):
                pass
        self.reader = self.writer = None


@dataclass
class HttpJsonClient:
    """Posts batches in the seeded ``fleet-json`` format (or any format with the same field names)."""

    host: str
    port: int
    identifier: str
    token: str
    path: str = "/fleet-json"
    timeout: float = 10.0
    connected: bool = True

    async def connect(self) -> None:
        self.connected = True

    def _post(self, body: bytes) -> int:
        req = urllib.request.Request(f"http://{self.host}:{self.port}{self.path}", data=body, method="POST",  # noqa: S310
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - configured gateway URL
                return int(resp.status)
        except urllib.error.HTTPError as exc:
            return int(exc.code)

    async def send(self, records: list[AvlRecordSpec]) -> int:
        body = json.dumps([{"device_id": self.identifier, "time": r.time.isoformat(), "lat": r.latitude,
                            "lon": r.longitude, "speed_kmh": r.speed_kmh, "heading": r.heading,
                            "alt": r.altitude, "sats": r.satellites, "ignition": _ignition(r)}
                           for r in records]).encode()
        status = await asyncio.to_thread(self._post, body)
        if status in (401, 403):
            raise Rejected(self.identifier)
        if status != 200:
            raise ConnectionError(f"HTTP {status}")
        return len(records)

    async def close(self) -> None:
        return None
