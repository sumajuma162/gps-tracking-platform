"""Redis integration: the positions stream (to the processor) and registry/listener change events."""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as aioredis

from gpsgw.normalization.normalized_location import NormalizedLocation
from gpsgw.services.ports import DeviceInfo

log = logging.getLogger("gpsgw.redis")
STREAM = "positions"
DEVICE_CHANNEL = "device_registry"
LISTENER_CHANNEL = "listener_ports"
COMMAND_CHANNEL = "commands"


class RedisStreamPublisher:
    """XADD each stored record to the ``positions`` stream. The stream is length-capped: the database is the
    source of truth and the processor catches up from it after an outage (spec A3)."""

    def __init__(self, client: aioredis.Redis, maxlen: int = 200_000) -> None:
        self.client = client
        self.maxlen = maxlen

    async def publish(self, device: DeviceInfo, records: list[NormalizedLocation]) -> None:
        async with self.client.pipeline(transaction=False) as pipe:
            for r in records:
                pipe.xadd(STREAM, {"device_id": device.device_id, "organization_id": device.organization_id,
                                   "vehicle_id": device.vehicle_id or "", "record": r.model_dump_json()},
                          maxlen=self.maxlen, approximate=True)
            await pipe.execute()


class NullPublisher:
    async def publish(self, device: DeviceInfo, records: list[NormalizedLocation]) -> None:
        return None


class EventSubscriber:
    """Applies backend change events. Reconnects with backoff; missed events are covered by the registry TTL
    and the listener manager's periodic reconcile."""

    def __init__(self, client: aioredis.Redis, on_device: Callable[[dict[str, Any]], Awaitable[None]],
                 on_listeners: Callable[[], Awaitable[None]],
                 on_command: Callable[[dict[str, Any]], Awaitable[None]] | None = None) -> None:
        self.client = client
        self.on_device = on_device
        self.on_listeners = on_listeners
        self.on_command = on_command
        self.ready = asyncio.Event()

    async def run(self) -> None:
        delay = 1.0
        while True:
            try:
                pubsub = self.client.pubsub()
                await pubsub.subscribe(DEVICE_CHANNEL, LISTENER_CHANNEL, COMMAND_CHANNEL)
                self.ready.set()
                delay = 1.0
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    channel = message["channel"].decode() if isinstance(message["channel"], bytes) \
                        else message["channel"]
                    try:
                        payload = json.loads(message["data"])
                    except (TypeError, ValueError):
                        continue
                    if channel == DEVICE_CHANNEL and isinstance(payload, dict):
                        await self.on_device(payload)
                    elif channel == LISTENER_CHANNEL:
                        await self.on_listeners()
                    elif channel == COMMAND_CHANNEL and self.on_command is not None and isinstance(payload, dict):
                        await self.on_command(payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.ready.clear()
                log.warning("event subscription lost; retrying", extra={"error": type(exc).__name__,
                                                                        "retry_in": delay})
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
