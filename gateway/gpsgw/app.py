"""Assembles a running gateway from its parts. Used by ``main`` and by integration tests."""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from gpsgw.config import GatewayConfig
from gpsgw.health import serve_health
from gpsgw.listeners.manager import ListenerManager
from gpsgw.listeners.tcp import TcpListener
from gpsgw.listeners.udp import UdpListener
from gpsgw.metrics import Metrics
from gpsgw.protocols.base import ProtocolRegistry, RawRecord, Transport, default_registry
from gpsgw.services.connections import ConnectionTracker
from gpsgw.services.pipeline import IngestPipeline

log = logging.getLogger("gpsgw")


def _manual_device_time(value: Any) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if not isinstance(value, str):
        raise ValueError("device_time must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("device_time must be an ISO 8601 string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("device_time must include a timezone")
    return parsed.astimezone(UTC)


class StartupError(RuntimeError):
    pass


@dataclass
class Gateway:
    config: GatewayConfig
    protocols: ProtocolRegistry = field(default_factory=default_registry)
    metrics: Metrics = field(default_factory=Metrics)
    tracker: ConnectionTracker = field(default_factory=ConnectionTracker)
    pipeline: IngestPipeline | None = None
    registry: Any = None
    inbox: Any = None
    connection_log: Any = None
    commands: Any = None
    manager: ListenerManager | None = None
    pool: Any = None
    redis: Any = None
    tasks: list[asyncio.Task[Any]] = field(default_factory=list)
    health: asyncio.Server | None = None

    formats: Any = None

    def make_listener(self, decoder: Any, transport: str, port: int) -> Any:
        assert self.pipeline is not None
        if transport == Transport.HTTP.value:
            from gpsgw.listeners.http import HttpListener
            return HttpListener(self.registry, self.pipeline, self.inbox, self.formats, port=port,
                                metrics=self.metrics)
        if transport == Transport.UDP.value:
            return UdpListener(decoder, self.registry, self.pipeline, self.inbox, port=port, metrics=self.metrics)
        from gpsgw.listeners.tcp import TcpLimits

        limits = TcpLimits(max_connections_per_ip=self.config.max_connections_per_ip,
                           max_connections=self.config.max_connections)
        return TcpListener(decoder, self.registry, self.pipeline, self.inbox, tracker=self.tracker, port=port,
                           connection_log=self.connection_log, metrics=self.metrics, commands=self.commands,
                           limits=limits)

    def _on_listeners(self, current: dict[tuple[int, str], str]) -> None:
        self.metrics.listeners.clear()
        for (_, transport), proto in current.items():
            self.metrics.listeners.labels(proto, transport).inc()

    async def _on_device_event(self, event: dict[str, Any]) -> None:
        identifier = str(event.get("identifier", ""))
        if not identifier:
            return
        if identifier == "*":  # a device model or IO profile changed: every cached entry may be stale
            self.registry.invalidate(None)
            return
        self.registry.invalidate(identifier)
        if self.pipeline and event.get("device_id"):
            self.pipeline.annotator.forget(int(event["device_id"]))
        if not event.get("active", True):
            closed = self.tracker.close_device(identifier)
            log.info("device disabled; connections closed", extra={"count": closed})

    async def start(self) -> None:
        cfg = self.config
        if cfg.storage == "memory":
            await self._start_memory()
        elif cfg.storage == "postgres":
            await self._start_postgres()
        else:
            raise StartupError(f"unknown GATEWAY_STORAGE: {cfg.storage}")
        from gpsgw.protocols.generic.format import parse_sample

        self.health = await serve_health(cfg.health_port, self.snapshot, host=cfg.listen_host,
                                         metrics=self.metrics.render, parse_sample=parse_sample,
                                         manual_position=self._manual_position,
                                         manual_token=cfg.manual_ingest_token)

    async def _manual_position(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.pipeline is None or self.registry is None:
            raise RuntimeError("gateway is not ready")
        identifier = str(payload.get("identifier", "")).strip()
        protocol = str(payload.get("protocol", "")).strip()
        if not identifier or not protocol:
            raise ValueError("identifier and protocol are required")
        try:
            latitude = float(payload["latitude"])
            longitude = float(payload["longitude"])
            speed = float(payload.get("speed_kmh", 0))
            heading_value = payload.get("heading")
            heading = None if heading_value is None else float(heading_value)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid position fields") from exc
        if not all(math.isfinite(value) for value in (latitude, longitude, speed)):
            raise ValueError("position fields must be finite numbers")
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180 or not 0 <= speed <= 300:
            raise ValueError("position fields are outside their valid range")
        if heading is not None and (not math.isfinite(heading) or not 0 <= heading < 360):
            raise ValueError("heading must be between 0 and 359.999")
        ignition = payload.get("ignition", True)
        if not isinstance(ignition, bool):
            raise ValueError("ignition must be a boolean")
        device = await self.registry.lookup(identifier, protocol)
        if device is None:
            raise LookupError(identifier)
        io: dict[int, int] = {}
        if device.profile:
            for mapping in device.profile.mappings:
                if mapping.kind == "field" and mapping.target == "ignition":
                    io[mapping.io_id] = int(ignition)
                elif mapping.kind == "field" and mapping.target == "motion":
                    io[mapping.io_id] = int(speed > 0)
        device_time = _manual_device_time(payload.get("device_time"))
        raw = RawRecord(device_time=device_time, latitude=latitude, longitude=longitude, speed_kmh=speed,
                        heading=heading, satellites=8, gps_valid=True, io=io,
                        extra={"manual_position": True,
                               "fields": {"ignition": ignition, "motion": speed > 0}})
        await self.pipeline.ingest(device, [raw])
        return {"accepted_records": 1, "latitude": latitude, "longitude": longitude,
                "device_time": device_time.isoformat()}

    async def _start_memory(self) -> None:
        from gpsgw.normalization.io_mapper import IoMappingProfile
        from gpsgw.services.memory import MemoryInbox, MemoryPersistence, MemoryPublisher, MemoryRegistry
        from gpsgw.services.ports import DeviceInfo

        cfg = self.config
        if not cfg.allow_memory_storage:
            raise StartupError("GATEWAY_STORAGE=memory keeps positions in RAM and would ACK records that are not "
                               "durable. Set GATEWAY_ALLOW_MEMORY_STORAGE=1 only for local development.")
        log.warning("MEMORY STORAGE: development only, data is lost on restart")
        profile = IoMappingProfile.load_builtin("teltonika_fmc920")
        self.registry = MemoryRegistry([DeviceInfo(i + 1, imei, "teltonika", 0, None, profile)
                                        for i, imei in enumerate(cfg.dev_devices)])
        self.inbox = MemoryInbox()
        self.pipeline = IngestPipeline(MemoryPersistence(), MemoryPublisher(), metrics=self.metrics)
        decoder = self.protocols.get("teltonika")
        for transport, port in (("tcp", cfg.teltonika_tcp_port), ("udp", cfg.teltonika_udp_port)):
            listener = self.make_listener(decoder, transport, port)
            await listener.start(cfg.listen_host)

    async def _start_postgres(self) -> None:
        from gpsgw.services import postgres
        from gpsgw.services.redis_bus import EventSubscriber, NullPublisher, RedisStreamPublisher

        cfg = self.config
        if not cfg.database_url:
            raise StartupError("DATABASE_URL is required for GATEWAY_STORAGE=postgres.")
        try:
            self.pool = await postgres.create_pool(cfg.database_url)
        except (OSError, Exception) as exc:
            raise StartupError(f"cannot connect to PostgreSQL: {type(exc).__name__}") from exc
        await postgres.sync_protocols(self.pool, self.protocols)
        self.registry = postgres.PostgresRegistry(self.pool)
        self.formats = postgres.PostgresFormats(self.pool)
        self.inbox = postgres.PostgresInbox(self.pool)
        self.connection_log = postgres.PostgresConnectionLog(self.pool)
        from gpsgw.services.commands import CommandService

        self.commands = CommandService(self.pool, self.tracker)
        publisher: Any = NullPublisher()
        if cfg.redis_url:
            import redis.asyncio as aioredis

            self.redis = aioredis.Redis.from_url(cfg.redis_url, socket_connect_timeout=3)
            publisher = RedisStreamPublisher(self.redis)
        else:
            log.warning("REDIS_URL not set: no hot reload events and no positions stream")
        self.pipeline = IngestPipeline(postgres.PostgresPersistence(self.pool), publisher, metrics=self.metrics)
        pool = self.pool
        self.manager = ListenerManager(self.protocols, self.make_listener,
                                       lambda: postgres.load_listener_specs(pool), host=cfg.listen_host,
                                       on_change=self._on_listeners)
        await self.manager.reconcile()
        self.tasks.append(asyncio.create_task(self.manager.run(cfg.reconcile_interval)))
        self.tasks.append(asyncio.create_task(self.commands.run()))
        if self.redis is not None:
            subscriber = EventSubscriber(self.redis, self._on_device_event, self._reconcile_now, self._on_command_event)
            self.tasks.append(asyncio.create_task(subscriber.run()))
            self.subscriber = subscriber

    async def _on_command_event(self, event: dict[str, Any]) -> None:
        if self.commands is not None and event.get("identifier"):
            await self.commands.deliver(str(event["identifier"]))

    async def _reconcile_now(self) -> None:
        if self.manager:
            await self.manager.reconcile()

    def snapshot(self) -> dict[str, Any]:
        listeners = sorted(f"{p}/{t}:{proto}" for (p, t), proto in
                           ((k, v[0]) for k, v in (self.manager.running.items() if self.manager else [])))
        return {"storage": self.config.storage, "protocols": self.protocols.names(), "listeners": listeners,
                "connections": self.tracker.count(),
                "stats": asdict(self.pipeline.stats) if self.pipeline else {}}

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.manager:
            await self.manager.stop_all()
        if self.health:
            self.health.close()
        if self.redis is not None:
            await self.redis.aclose()
        if self.pool is not None:
            await self.pool.close()
