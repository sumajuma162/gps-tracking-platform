"""A deliberately small HTTP/1.1 reader/writer for the gateway's own endpoints (no framework, bounded sizes)."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

REASONS = {200: "OK", 202: "Accepted", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
           404: "Not Found", 405: "Method Not Allowed", 411: "Length Required", 413: "Payload Too Large",
           429: "Too Many Requests", 500: "Internal Server Error", 503: "Service Unavailable"}


class HttpError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: bytes = b""
    extra: dict[str, Any] = field(default_factory=dict)

    def form(self) -> dict[str, str]:
        """Query string merged with a urlencoded body (OsmAnd-style trackers send either)."""
        out = dict(self.query)
        if self.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
            out.update(dict(parse_qsl(self.body.decode(errors="replace"), keep_blank_values=True)))
        return out


async def read_request(reader: asyncio.StreamReader, max_body: int, timeout: float = 10.0) -> Request:  # noqa: ASYNC109
    line = await asyncio.wait_for(reader.readline(), timeout)
    if not line:
        raise ConnectionError("closed")
    parts = line.decode("latin-1").split()
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        raise HttpError(400, "bad_request_line")
    headers: dict[str, str] = {}
    for _ in range(64):
        raw = await asyncio.wait_for(reader.readline(), timeout)
        if raw in (b"\r\n", b"\n", b""):
            break
        if len(raw) > 4096:
            raise HttpError(400, "header_too_long")
        name, _, value = raw.decode("latin-1").partition(":")
        headers[name.strip().lower()] = value.strip()
    else:
        raise HttpError(400, "too_many_headers")
    body = b""
    if parts[0] in ("POST", "PUT"):
        if "content-length" not in headers:
            raise HttpError(411, "length_required")
        try:
            length = int(headers["content-length"])
        except ValueError as exc:
            raise HttpError(400, "bad_length") from exc
        if length < 0 or length > max_body:
            raise HttpError(413, "too_large")
        body = await asyncio.wait_for(reader.readexactly(length), timeout)
    url = urlsplit(parts[1])
    return Request(parts[0], unquote(url.path), dict(parse_qsl(url.query, keep_blank_values=True)), headers, body)


async def respond(writer: asyncio.StreamWriter, status: int, payload: bytes | str | dict[str, Any],
                  content_type: str | None = None) -> None:
    if isinstance(payload, dict):
        body, ctype = json.dumps(payload, default=str).encode(), "application/json"
    elif isinstance(payload, str):
        body, ctype = payload.encode(), "text/plain; charset=utf-8"
    else:
        body, ctype = payload, "application/octet-stream"
    writer.write(f"HTTP/1.1 {status} {REASONS.get(status, 'Error')}\r\nContent-Type: {content_type or ctype}\r\n"
                 f"Content-Length: {len(body)}\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n".encode()
                 + body)
    await writer.drain()
