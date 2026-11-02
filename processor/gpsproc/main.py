"""Processor entrypoint: stream consumer + status sweep + /health."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from typing import Any

from gpsproc.app import Processor, ProcessorConfig

log = logging.getLogger("gpsproc")


#: Stats that only grow (Prometheus counters); everything else is a gauge.
COUNTERS = {"entries", "duplicates", "late_skipped", "status_changes", "updates_sent", "errors", "group_recreated",
            "analysis_items", "recompute_requests", "recomputes_run", "alerts_touched", "webhook_events",
            "caught_up"}


def render_metrics(snapshot: dict[str, Any]) -> bytes:
    """Prometheus text format from the processor's stats (spec A15)."""
    lines = []
    for name, value in snapshot.get("stats", {}).items():
        if not isinstance(value, int | float):
            continue
        metric = f"gpsproc_{name}_total" if name in COUNTERS else f"gpsproc_{name}"
        lines += [f"# TYPE {metric} {'counter' if name in COUNTERS else 'gauge'}", f"{metric} {float(value)}"]
    return ("\n".join(lines) + "\n").encode()


async def serve_health(port: int, processor: Processor, host: str = "0.0.0.0") -> asyncio.Server:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), 5)
            parts = request_line.split()
            if len(parts) > 1 and parts[1] == b"/metrics":
                payload = render_metrics(processor.snapshot())
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; version=0.0.4\r\n"
                             + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode() + payload)
                await writer.drain()
                return
            body: dict[str, Any] = {"status": "ok", **processor.snapshot()}
            ok = True
            try:
                assert processor.redis is not None and processor.pool is not None
                await processor.redis.ping()
                await processor.pool.fetchval("SELECT 1")
            except Exception as exc:
                ok, body["status"], body["error"] = False, "degraded", type(exc).__name__
            payload = json.dumps(body).encode()
            writer.write(f"HTTP/1.1 {200 if ok else 503} X\r\nContent-Type: application/json\r\n"
                         f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode() + payload)
            await writer.drain()
        except (TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()

    return await asyncio.start_server(handle, host, port)


async def run() -> None:
    config = ProcessorConfig.from_env()
    if not config.database_url or not config.redis_url:
        log.critical("DATABASE_URL and REDIS_URL are required")
        sys.exit(2)
    processor = Processor(config)
    await processor.start()
    server = await serve_health(int(os.environ.get("PROCESSOR_HEALTH_PORT", "8082")), processor,
                                os.environ.get("PROCESSOR_LISTEN_HOST", "0.0.0.0"))
    log.info("processor ready")
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await stop.wait()
    server.close()
    await processor.stop()


if __name__ == "__main__":
    from gpsproc.jsonlog import configure

    configure()
    if os.environ.get("SENTRY_DSN"):
        import sentry_sdk

        sentry_sdk.init(dsn=os.environ["SENTRY_DSN"], environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
                        send_default_pii=False)
    asyncio.run(run())
