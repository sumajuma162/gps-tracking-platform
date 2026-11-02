"""Asyncio TCP listener: one protocol per port, one session per connection."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from gpsgw.metrics import Metrics
from gpsgw.protocols.base import BaseProtocolDecoder, Frame, FrameKind, ProtocolError, SessionContext, Transport
from gpsgw.services.connections import ConnectionTracker
from gpsgw.services.pipeline import IngestPipeline, PersistenceFailed
from gpsgw.services.ports import ConnectionLog, DeviceInfo, DeviceRegistry, UnknownAttempt, UnknownDeviceInbox

log = logging.getLogger("gpsgw.tcp")


@dataclass(frozen=True)
class TcpLimits:
    handshake_timeout: float = 30.0
    idle_timeout: float = 300.0
    read_chunk: int = 4096
    max_connections_per_ip: int = 50
    #: All TCP device connections of this gateway process, across listeners (spec A4.11 global limit).
    max_connections: int = 10_000


def mask(identifier: str) -> str:
    return identifier[:4] + "*" * max(0, len(identifier) - 8) + identifier[-4:]


@dataclass
class _Session:
    device: DeviceInfo | None = None
    connection_id: int | None = None
    packets: int = 0
    records: int = 0
    close_reason: str = "device_closed"


class TcpListener:
    transport_name = "tcp"

    def __init__(self, decoder: BaseProtocolDecoder, registry: DeviceRegistry,
                 pipeline: IngestPipeline, inbox: UnknownDeviceInbox,
                 tracker: ConnectionTracker | None = None, limits: TcpLimits | None = None,
                 port: int = 0, connection_log: ConnectionLog | None = None, metrics: Metrics | None = None,
                 commands: Any = None) -> None:
        self.decoder = decoder
        self.registry = registry
        self.pipeline = pipeline
        self.inbox = inbox
        self.tracker = tracker or ConnectionTracker()
        self.limits = limits or TcpLimits()
        self.port = port
        self.connection_log = connection_log
        self.commands = commands
        self.metrics = metrics or pipeline.metrics
        self._per_ip: dict[str, int] = {}
        self.server: asyncio.base_events.Server | None = None

    async def start(self, host: str = "0.0.0.0") -> int:
        self.server = await asyncio.start_server(self.handle, host, self.port)
        self.port = self.server.sockets[0].getsockname()[1]
        log.info("tcp listener started", extra={"protocol": self.decoder.name, "port": self.port})
        return self.port

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _raw(self, s: _Session, direction: str, payload: bytes, result: str) -> None:
        if s.device and s.device.log_raw_packets:
            try:
                await self.pipeline.persistence.record_raw(s.device, direction, "tcp", payload, result)
            except Exception as exc:  # diagnostics must never break ingestion
                log.warning("raw packet not stored", extra={"error": type(exc).__name__})

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer_info = writer.get_extra_info("peername") or ("?", 0)
        ip = str(peer_info[0])
        if self._per_ip.get(ip, 0) >= self.limits.max_connections_per_ip \
                or self.tracker.sockets >= self.limits.max_connections:
            self.metrics.protocol_errors.labels(self.decoder.name, "connection_limit").inc()
            writer.close()
            return
        self._per_ip[ip] = self._per_ip.get(ip, 0) + 1
        self.tracker.sockets += 1
        ctx = SessionContext(Transport.TCP, f"{ip}:{peer_info[1]}")
        s = _Session()
        buffer = bytearray()
        try:
            while True:
                timeout = self.limits.idle_timeout if s.device else self.limits.handshake_timeout
                chunk = await asyncio.wait_for(reader.read(self.limits.read_chunk), timeout)
                if not chunk:
                    return
                buffer += chunk
                if len(buffer) > self.decoder.max_frame_size + 16:
                    raise ProtocolError("oversized", str(len(buffer)))
                while (frame := self.decoder.extract_frame(buffer, ctx)) is not None:
                    if frame.kind is FrameKind.HANDSHAKE:
                        if not await self._handshake(frame, ctx, writer, s):
                            s.close_reason = "unknown_device"
                            return
                    elif frame.kind is FrameKind.DATA:
                        if not await self._data(frame, ctx, writer, s):
                            return
                    elif frame.kind is FrameKind.COMMAND_RESPONSE:
                        resp = self.decoder.decode_command_response(frame)
                        ctx.state.setdefault("command_responses", []).append(resp)
                        await self._raw(s, "in", frame.raw, "command_response")
                        if self.commands is not None and ctx.identity is not None and resp is not None:
                            await self.commands.on_response(ctx.identity.identifier, resp.text)
        except ProtocolError as exc:
            s.close_reason = f"protocol:{exc.code}"[:40]
            self.pipeline.stats.bump(self.pipeline.stats.protocol_errors, exc.code)
            self.metrics.protocol_errors.labels(self.decoder.name, exc.code).inc()
            log.warning("protocol error", extra={"peer": ctx.peer, "code": exc.code})
        except TimeoutError:
            s.close_reason = "timeout"
        except (ConnectionError, asyncio.IncompleteReadError):
            s.close_reason = "connection_lost"
        finally:
            await self._finish(ip, ctx, writer, s)

    async def _data(self, frame: Frame, ctx: SessionContext, writer: asyncio.StreamWriter, s: _Session) -> bool:
        assert s.device is not None and ctx.identity is not None
        # Re-read the registry (cached) so disable, vehicle changes and IO profile edits apply to this packet.
        current = await self.registry.lookup(ctx.identity.identifier, self.decoder.name)
        if current is None:
            s.close_reason = "device_disabled"
            return False
        s.device = current
        try:
            raws = self.decoder.decode(frame, ctx)
        except ProtocolError as exc:
            await self._raw(s, "in", frame.raw, f"error:{exc.code}")
            raise
        self.metrics.packets.labels(self.decoder.name, "tcp").inc()
        try:
            accepted = await self.pipeline.ingest(current, raws)
        except PersistenceFailed:
            s.close_reason = "storage_unavailable"
            await self._raw(s, "in", frame.raw, "not_stored")
            return False  # no ACK: the device keeps the records and resends them
        s.packets += 1
        s.records += len(raws)
        await self._raw(s, "in", frame.raw, f"ok:{len(raws)}")
        ack = self.decoder.acknowledge(frame, accepted, ctx)
        if ack:
            writer.write(ack)
            await writer.drain()
            await self._raw(s, "out", ack, "ack")
        return True

    async def _handshake(self, frame: Frame, ctx: SessionContext, writer: asyncio.StreamWriter,
                         s: _Session) -> bool:
        identity = self.decoder.identify_device(frame)
        device = await self.registry.lookup(identity.identifier, self.decoder.name) if identity else None
        response = self.decoder.handshake_response(device is not None, frame)
        if response:
            writer.write(response)
            await writer.drain()
        if device is None or identity is None:
            self.pipeline.stats.unknown_devices += 1
            self.metrics.unknown_devices.labels(self.decoder.name).inc()
            if identity:
                await self.inbox.record(UnknownAttempt(
                    identifier=identity.identifier, protocol=self.decoder.name, transport="tcp",
                    port=self.port, peer=ctx.peer, seen_at=datetime.now(UTC), sample_hex=frame.raw.hex()[:256],
                ))
                log.info("unknown device rejected", extra={"imei": mask(identity.identifier)})
            return False
        ctx.identity = identity
        s.device = device
        self.tracker.add(identity.identifier, writer)
        self.metrics.connections.labels(self.decoder.name).inc()
        if self.connection_log:
            try:
                s.connection_id = await self.connection_log.opened(device, "tcp", self.port, ctx.peer)
            except Exception as exc:
                log.warning("connection not logged", extra={"error": type(exc).__name__})
        await self._raw(s, "in", frame.raw, "handshake")
        if self.commands is not None:          # commands queued while the tracker was offline go out now
            asyncio.get_running_loop().create_task(self.commands.deliver(identity.identifier))
        return True

    async def _finish(self, ip: str, ctx: SessionContext, writer: asyncio.StreamWriter, s: _Session) -> None:
        self._per_ip[ip] -= 1
        self.tracker.sockets -= 1
        if not self._per_ip[ip]:
            del self._per_ip[ip]
        if ctx.identity and s.device:
            self.tracker.remove(ctx.identity.identifier, writer)
            self.metrics.connections.labels(self.decoder.name).dec()
        if self.connection_log and s.connection_id is not None:
            try:
                await self.connection_log.closed(s.connection_id, s.close_reason, s.packets, s.records)
            except Exception as exc:
                log.warning("connection close not logged", extra={"error": type(exc).__name__})
        writer.close()
