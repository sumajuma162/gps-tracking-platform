"""Minimal HTTP endpoint for container healthchecks, Prometheus scraping and the generic-format test parse.

``POST /test-parse`` ``{"definition": {...}, "sample": "..."}`` runs the gateway's own parser on a sample message
(spec A7), so the web app never parses tracker data itself. The port is internal (not published).
"""
from __future__ import annotations

import asyncio
import hmac
import json
from collections.abc import Awaitable, Callable
from typing import Any

from gpsgw.listeners.httpio import HttpError, read_request, respond


async def serve_health(port: int, snapshot: Callable[[], dict[str, Any]], host: str = "0.0.0.0",
                       metrics: Callable[[], bytes] | None = None,
                       parse_sample: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
                       manual_position: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
                       manual_token: str = "") -> asyncio.Server:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await read_request(reader, max_body=128 * 1024, timeout=5)
            if request.path in ("/health", "/healthz"):
                await respond(writer, 200, {"status": "ok", **snapshot()})
            elif request.path == "/metrics" and metrics is not None:
                await respond(writer, 200, metrics(), "text/plain; version=0.0.4")
            elif request.path == "/test-parse" and request.method == "POST" and parse_sample is not None:
                try:
                    data = json.loads(request.body)
                    result = parse_sample(dict(data["definition"]), str(data["sample"]))
                except (ValueError, KeyError, TypeError) as exc:
                    raise HttpError(400, "bad_request") from exc
                await respond(writer, 200, result)
            elif request.path == "/manual-position" and request.method == "POST" and manual_position and manual_token:
                if not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {manual_token}"):
                    raise HttpError(401, "unauthorized")
                try:
                    data = json.loads(request.body)
                    if not isinstance(data, dict):
                        raise ValueError("expected an object")
                    result = await manual_position(data)
                except LookupError as exc:
                    raise HttpError(404, "device_not_found") from exc
                except (ValueError, KeyError, TypeError) as exc:
                    raise HttpError(400, "bad_request") from exc
                except Exception as exc:
                    raise HttpError(503, "ingest_failed") from exc
                await respond(writer, 200, result)
            else:
                await respond(writer, 404, {"error": "not_found"})
        except HttpError as exc:
            await respond(writer, exc.status, {"error": exc.code})
        except (TimeoutError, ConnectionError, asyncio.IncompleteReadError, UnicodeDecodeError):
            pass
        finally:
            writer.close()

    return await asyncio.start_server(handle, host, port)
