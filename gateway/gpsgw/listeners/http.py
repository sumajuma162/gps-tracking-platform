"""HTTP ingestion for UI-defined JSON formats (spec A7 "Generic configurable protocol").

``POST /<format slug>`` with a JSON body (one message, or a list), or ``GET``/form-encoded ``POST`` with the values
as parameters. Authentication follows the format: a per-device token (``Authorization: Bearer``, ``X-Device-Token``
or ``token=``), or the device identifier itself, accepted only for registered, active devices of that format
(the allowlist). The same rules as every listener apply: size limits, a per-IP rate limit, unknown identifiers go
to the inbox, and the response (the format's ACK) is sent only after the records are stored. A storage failure
answers 503 so the tracker retries.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from gpsgw.listeners.httpio import HttpError, Request, read_request, respond
from gpsgw.metrics import Metrics
from gpsgw.protocols.base import ProtocolError, RawRecord
from gpsgw.protocols.generic.format import GenericFormat, ParseError, json_messages, parse_record
from gpsgw.services.pipeline import IngestPipeline, PersistenceFailed
from gpsgw.services.ports import DeviceInfo, UnknownAttempt

log = logging.getLogger("gpsgw.http")
PROTOCOL = "generic_json"


@dataclass(frozen=True)
class HttpLimits:
    max_body: int = 256 * 1024
    #: Token bucket per source IP: sustained requests per second and burst.
    rate: float = 5.0
    burst: int = 30
    timeout: float = 10.0


class _Bucket:
    __slots__ = ("tokens", "at")

    def __init__(self, burst: int) -> None:
        self.tokens, self.at = float(burst), time.monotonic()


class HttpListener:
    transport_name = "http"

    def __init__(self, registry: Any, pipeline: IngestPipeline, inbox: Any, formats: Any, port: int = 0,
                 limits: HttpLimits | None = None, metrics: Metrics | None = None) -> None:
        self.registry, self.pipeline, self.inbox, self.formats = registry, pipeline, inbox, formats
        self.port = port
        self.limits = limits or HttpLimits()
        self.metrics = metrics or pipeline.metrics
        self._buckets: dict[str, _Bucket] = {}
        self.server: asyncio.base_events.Server | None = None

    async def start(self, host: str = "0.0.0.0") -> int:
        self.server = await asyncio.start_server(self.handle, host, self.port)
        self.port = self.server.sockets[0].getsockname()[1]
        log.info("http listener started", extra={"protocol": PROTOCOL, "port": self.port})
        return self.port

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    def _allow(self, ip: str) -> bool:
        b = self._buckets.get(ip)
        if b is None:
            if len(self._buckets) > 10_000:
                self._buckets.clear()
            b = self._buckets[ip] = _Bucket(self.limits.burst)
        now = time.monotonic()
        b.tokens = min(self.limits.burst, b.tokens + (now - b.at) * self.limits.rate)
        b.at = now
        if b.tokens < 1:
            return False
        b.tokens -= 1
        return True

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername") or ("?", 0)
        ip = str(peer[0])
        try:
            request = await read_request(reader, self.limits.max_body, self.limits.timeout)
            if not self._allow(ip):
                raise HttpError(429, "rate_limited")
            status, body = await self._serve(request, f"{ip}:{peer[1]}")
            await respond(writer, status, body)
        except HttpError as exc:
            self.metrics.protocol_errors.labels(PROTOCOL, exc.code).inc()
            await _safe(respond(writer, exc.status, {"error": exc.code}))
        except ParseError as exc:
            self.metrics.protocol_errors.labels(PROTOCOL, exc.code).inc()
            await _safe(respond(writer, 400, {"error": "invalid_message", "field": exc.field, "detail": exc.detail}))
        except (TimeoutError, ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    async def _serve(self, request: Request, peer: str) -> tuple[int, Any]:
        if request.method not in ("GET", "POST"):
            raise HttpError(405, "method_not_allowed")
        slug = request.path.strip("/")
        definition = await self.formats.get(slug) if slug and "/" not in slug else None
        if definition is None:
            raise HttpError(404, "unknown_format")
        try:
            fmt = GenericFormat.model_validate({**definition, "slug": slug})
        except ValidationError as exc:
            log.error("invalid generic format", extra={"format": slug, "error": str(exc)[:200]})
            raise HttpError(500, "format_invalid") from exc
        if fmt.kind != "json":
            raise HttpError(404, "unknown_format")
        now = datetime.now(UTC)
        form = request.form()
        if request.method == "POST" and request.headers.get("content-type", "").startswith("application/json"):
            messages = json_messages(fmt, request.body)
        else:
            messages = [form]
        parsed = [parse_record(fmt, m, now) for m in messages]
        device = await self._authenticate(fmt, request, form, parsed, peer, now)
        self.metrics.packets.labels(PROTOCOL, "http").inc()
        try:
            await self.pipeline.ingest(device, [raw for _, raw in parsed])
        except PersistenceFailed as exc:
            raise HttpError(503, "storage_unavailable") from exc
        return 200, fmt.ack if fmt.ack is not None else {"accepted": len(parsed)}

    async def _authenticate(self, fmt: GenericFormat, request: Request, form: dict[str, str],
                            parsed: list[tuple[str, RawRecord]], peer: str, now: datetime) -> DeviceInfo:
        identifiers = {identifier for identifier, _ in parsed}
        if len(identifiers) != 1:
            raise HttpError(400, "one_device_per_request")
        identifier = identifiers.pop()
        if fmt.auth == "token":
            auth = request.headers.get("authorization", "")
            token = (auth[7:].strip() if auth.lower().startswith("bearer ") else "") \
                or request.headers.get("x-device-token", "") or form.get("token", "")
            if not token:
                raise HttpError(401, "token_required")
            device = await self.registry.lookup_token(token)
            if device is None or device.identifier != identifier:
                raise HttpError(401, "invalid_token")
        else:
            device = await self.registry.lookup(identifier, PROTOCOL)
            if device is None:
                self.metrics.unknown_devices.labels(PROTOCOL).inc()
                await self.inbox.record(UnknownAttempt(identifier=identifier, protocol=PROTOCOL, transport="http",
                                                       port=self.port, peer=peer, seen_at=now,
                                                       sample_hex=request.body[:128].hex()))
                raise HttpError(401, "unknown_device")
        if device.format_slug and device.format_slug != fmt.slug:
            raise HttpError(403, "wrong_format")
        found: DeviceInfo = device
        return found


async def _safe(coro: Any) -> None:
    try:
        await coro
    except (ConnectionError, OSError):
        pass


__all__ = ["HttpLimits", "HttpListener", "ProtocolError"]
