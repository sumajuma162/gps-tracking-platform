"""Starts and stops listeners to match the ``listener_ports`` table (spec A7: within 10 seconds)."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from gpsgw.protocols.base import ProtocolRegistry, Transport
from gpsgw.services.postgres import ListenerSpec

log = logging.getLogger("gpsgw.listeners")


class Listener(Protocol):
    port: int

    async def start(self, host: str = ...) -> int: ...

    async def stop(self) -> None: ...


ListenerFactory = Callable[[Any, str, int], Listener]


class ListenerManager:
    def __init__(self, protocols: ProtocolRegistry, factory: ListenerFactory,
                 source: Callable[[], Awaitable[list[ListenerSpec]]], host: str = "0.0.0.0",
                 on_change: Callable[[dict[tuple[int, str], str]], None] | None = None) -> None:
        self.protocols = protocols
        self.factory = factory
        self.source = source
        self.host = host
        self.on_change = on_change
        self.running: dict[tuple[int, str], tuple[str, Listener]] = {}
        self.failed: dict[tuple[int, str], str] = {}
        self._lock = asyncio.Lock()

    def _wanted(self, specs: list[ListenerSpec]) -> dict[tuple[int, str], str]:
        wanted: dict[tuple[int, str], str] = {}
        self._options: dict[tuple[int, str], dict[str, Any]] = {}
        for spec in specs:
            if spec.transport not in (Transport.TCP.value, Transport.UDP.value, Transport.HTTP.value):
                continue
            try:
                decoder = self.protocols.get(spec.protocol)
            except KeyError:
                continue  # configured in the catalog, but this gateway has no decoder for it yet
            if Transport(spec.transport) in decoder.transports:
                key = (spec.port, spec.transport)
                # A listener whose options change (e.g. its generic format was edited) is restarted.
                wanted[key] = spec.protocol + (f"#{spec.options_key}" if spec.options else "")
                self._options[key] = spec.options
        return wanted

    def _decoder(self, key: tuple[int, str], proto: str) -> Any:
        decoder = self.protocols.get(proto.split("#", 1)[0])
        options = self._options.get(key) or {}
        configure = getattr(decoder, "configure", None)
        return configure(options) if configure is not None else decoder

    async def reconcile(self) -> dict[tuple[int, str], str]:
        async with self._lock:
            try:
                wanted = self._wanted(await self.source())
            except Exception as exc:
                log.warning("listener configuration unavailable; keeping current listeners",
                            extra={"error": type(exc).__name__})
                return {key: proto for key, (proto, _) in self.running.items()}
            for key in list(self.running):
                proto, listener = self.running[key]
                if wanted.get(key) != proto:
                    await listener.stop()
                    del self.running[key]
                    log.info("listener stopped", extra={"port": key[0], "transport": key[1], "protocol": proto})
            for key, proto in wanted.items():
                if key in self.running:
                    continue
                try:
                    listener = self.factory(self._decoder(key, proto), key[1], key[0])
                    await listener.start(self.host)
                except ValueError as exc:  # e.g. a generic text port without a usable format
                    self.failed[key] = str(exc)
                    log.error("listener not configured", extra={"port": key[0], "error": str(exc)[:200]})
                    continue
                except OSError as exc:  # port in use, permission denied: retried on the next reconcile
                    self.failed[key] = str(exc)
                    log.error("listener failed to start", extra={"port": key[0], "transport": key[1],
                                                                 "error": str(exc)})
                    continue
                self.failed.pop(key, None)
                self.running[key] = (proto, listener)
            current = {key: proto for key, (proto, _) in self.running.items()}
            if self.on_change:
                self.on_change(current)
            return current

    async def run(self, interval: float = 10.0) -> None:
        while True:
            await asyncio.sleep(interval)
            await self.reconcile()

    async def stop_all(self) -> None:
        async with self._lock:
            for _, listener in self.running.values():
                await listener.stop()
            self.running.clear()
