"""Asyncio UDP listener for protocols whose datagrams carry the device id."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from gpsgw.metrics import Metrics
from gpsgw.protocols.base import ProtocolError, SessionContext, Transport
from gpsgw.services.pipeline import IngestPipeline, PersistenceFailed
from gpsgw.services.ports import DeviceRegistry, UnknownAttempt, UnknownDeviceInbox

log = logging.getLogger("gpsgw.udp")


class UdpListener(asyncio.DatagramProtocol):
    transport_name = "udp"

    def __init__(self, decoder: Any, registry: DeviceRegistry, pipeline: IngestPipeline,
                 inbox: UnknownDeviceInbox, port: int = 0, metrics: Metrics | None = None) -> None:
        if not hasattr(decoder, "parse_datagram"):
            raise TypeError(f"{decoder.name} does not support UDP")
        self.decoder = decoder
        self.registry = registry
        self.pipeline = pipeline
        self.inbox = inbox
        self.port = port
        self.metrics = metrics or pipeline.metrics
        self.transport: asyncio.DatagramTransport | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    async def start(self, host: str = "0.0.0.0") -> int:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(lambda: self, local_addr=(host, self.port))
        self.port = transport.get_extra_info("sockname")[1]
        log.info("udp listener started", extra={"protocol": self.decoder.name, "port": self.port})
        return self.port

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        task = asyncio.get_running_loop().create_task(self._handle(data, addr))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle(self, data: bytes, addr: tuple[str, int]) -> None:
        peer = f"{addr[0]}:{addr[1]}"
        ctx = SessionContext(Transport.UDP, peer)
        try:
            identity, frame = self.decoder.parse_datagram(data)
            device = await self.registry.lookup(identity.identifier, self.decoder.name)
            if device is None:
                self.pipeline.stats.unknown_devices += 1
                self.metrics.unknown_devices.labels(self.decoder.name).inc()
                await self.inbox.record(UnknownAttempt(
                    identifier=identity.identifier, protocol=self.decoder.name, transport="udp",
                    port=self.port, peer=peer, seen_at=datetime.now(UTC), sample_hex=data.hex()[:256],
                ))
                return
            ctx.identity = identity
            raws = self.decoder.decode(frame, ctx)
            self.metrics.packets.labels(self.decoder.name, "udp").inc()
            accepted = await self.pipeline.ingest(device, raws)
            ack = self.decoder.acknowledge(frame, accepted, ctx)
            if device.log_raw_packets:
                await self.pipeline.persistence.record_raw(device, "in", "udp", data, f"ok:{len(raws)}")
            if ack and self.transport:
                self.transport.sendto(ack, addr)
                if device.log_raw_packets:
                    await self.pipeline.persistence.record_raw(device, "out", "udp", ack, "ack")
        except ProtocolError as exc:
            self.pipeline.stats.bump(self.pipeline.stats.protocol_errors, exc.code)
            self.metrics.protocol_errors.labels(self.decoder.name, exc.code).inc()
            log.warning("udp protocol error", extra={"peer": peer, "code": exc.code})
        except PersistenceFailed:
            return

    async def stop(self) -> None:
        if self.transport:
            self.transport.close()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
