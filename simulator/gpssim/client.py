"""Teltonika tracker clients speaking the real protocol over TCP or UDP (spec A16: never touch the database)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from gpsgw.protocols.teltonika.avl import CODEC_8E
from gpsgw.protocols.teltonika.codec12 import decode_command, encode_response
from gpsgw.protocols.teltonika.encoder import AvlRecordSpec, encode_imei, encode_tcp_packet, encode_udp_packet


def canned_response(command: str, imei: str) -> str:
    """Replies shaped like Teltonika's documented formats. Simulator output, not real device output."""
    now = datetime.now(UTC)
    cmd = command.strip().lower()
    if cmd == "getinfo":
        return (f"RTC:{now:%Y/%m/%d %H:%M} Init:{now:%Y/%m/%d %H:%M} UpTime:3600s PWR:PwrVoltage RST:0 GPS:3 SAT:11 "
                "TTFF:24 TTLF:0 NOGPS:0:0 SR:0 FG:0 FL:0 SMS:0 REC:0 MD:0 DB:0")
    if cmd == "getver":
        return f"Ver:03.28.07_08 GPS:AXN_5.10_3333 Hw:FMC920 Mod:11 IMEI:{imei} Init:{now:%Y-%m-%d %H:%M} BL:1.10"
    if cmd == "getgps":
        return f"GPS:1 Sat:11 Lat:-6.8160 Long:39.2803 Alt:22 Speed:0 Dir:0 Date: {now:%Y/%m/%d} Time: {now:%H:%M:%S}"
    if cmd == "getstatus":
        return ("Data Link: 1 GPRS: 1 Phone: 0 SIM: 0 OP: 64002 Signal: 4 NewSMS: 0 Roaming: 0 SMSFull: 0 LAC: 1 "
                "Cell ID: 1 NetType: 1 FwUpd:-1")
    return f"Unknown command: {command[:40]}"


class Rejected(Exception):
    """The server refused this IMEI (handshake reply 0x00)."""


@dataclass
class TcpClient:
    host: str
    port: int
    imei: str
    codec: int = CODEC_8E
    timeout: float = 10.0

    def __post_init__(self) -> None:
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.commands_received: list[str] = []

    @property
    def connected(self) -> bool:
        return self.writer is not None and not self.writer.is_closing()

    async def connect(self) -> None:
        self.reader, self.writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), self.timeout)
        self.writer.write(encode_imei(self.imei))
        await self.writer.drain()
        reply = await asyncio.wait_for(self.reader.readexactly(1), self.timeout)
        if reply != b"\x01":
            await self.close()
            raise Rejected(self.imei)

    async def send(self, records: list[AvlRecordSpec]) -> int:
        """Send one AVL packet and return the acknowledged record count."""
        return await self.send_raw(encode_tcp_packet(records, self.codec))

    async def send_raw(self, packet: bytes) -> int:
        assert self.reader is not None and self.writer is not None
        self.writer.write(packet)
        await self.writer.drain()
        while True:                  # a Codec 12 command may arrive before the ACK: answer it, keep waiting
            head = await asyncio.wait_for(self.reader.readexactly(4), self.timeout)
            if head != b"\x00\x00\x00\x00":
                return int.from_bytes(head, "big")
            await self._answer_command()

    async def _answer_command(self) -> None:
        assert self.reader is not None and self.writer is not None
        length = int.from_bytes(await asyncio.wait_for(self.reader.readexactly(4), self.timeout), "big")
        data = await asyncio.wait_for(self.reader.readexactly(length + 4), self.timeout)   # data + CRC
        text = decode_command(data[:length])
        self.commands_received.append(text)
        self.writer.write(encode_response(canned_response(text, self.imei)))
        await self.writer.drain()

    async def idle(self, seconds: float) -> None:
        """Wait like a connected device: answer any commands the server sends meanwhile."""
        if not self.connected or self.reader is None:
            await asyncio.sleep(seconds)
            return
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        while (left := end - loop.time()) > 0:
            try:
                head = await asyncio.wait_for(self.reader.readexactly(4), left)
            except (TimeoutError, asyncio.IncompleteReadError):
                return
            if head == b"\x00\x00\x00\x00":
                await self._answer_command()

    async def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except (ConnectionError, OSError):
                pass
        self.reader = self.writer = None


@dataclass
class UdpClient:
    host: str
    port: int
    imei: str
    codec: int = CODEC_8E
    timeout: float = 10.0

    def __post_init__(self) -> None:
        self.packet_id = 1
        self.connected = True

    async def connect(self) -> None:
        self.connected = True

    async def send(self, records: list[AvlRecordSpec]) -> int:
        loop = asyncio.get_running_loop()
        got: asyncio.Queue[bytes] = asyncio.Queue()

        class Proto(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: object) -> None:
                got.put_nowait(data)

        transport, _ = await loop.create_datagram_endpoint(Proto, remote_addr=(self.host, self.port))
        avl_id = self.packet_id % 256
        try:
            transport.sendto(encode_udp_packet(self.imei, records, packet_id=self.packet_id & 0xFFFF,
                                               avl_packet_id=avl_id, codec=self.codec))
            ack = await asyncio.wait_for(got.get(), self.timeout)
        except TimeoutError as exc:  # unknown IMEIs get no UDP reply at all
            raise Rejected(self.imei) from exc
        finally:
            transport.close()
            self.packet_id += 1
        return ack[-1]

    async def send_raw(self, packet: bytes) -> int:
        raise NotImplementedError("raw packets are TCP-only in the simulator")

    async def close(self) -> None:
        self.connected = False
