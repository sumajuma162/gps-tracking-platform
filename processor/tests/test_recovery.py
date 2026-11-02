"""The consumer recovers when Redis loses the stream and consumer group (needs a local Redis; skipped otherwise)."""
from __future__ import annotations

import asyncio
import json
import logging
import os

import pytest

from gpsproc.app import GROUP, STREAM, Processor, ProcessorConfig
from gpsproc.jsonlog import JsonFormatter


def _isolated(url: str, db: int = 15) -> str:
    """Same server, separate database: never collides with a dev stack running on database 0."""
    base, _, last = url.rpartition("/")
    return f"{base}/{db}" if last.isdigit() else f"{url.rstrip('/')}/{db}"


REDIS_URL = _isolated(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))


async def redis_or_skip():  # type: ignore[no-untyped-def]
    import redis.asyncio as aioredis
    client = aioredis.Redis.from_url(REDIS_URL, socket_connect_timeout=1)
    try:
        await client.ping()
    except Exception:
        pytest.skip("Redis not available")
    return client


async def test_recreates_the_group_after_redis_loses_it():
    client = await redis_or_skip()
    await client.delete(STREAM)
    proc = Processor(ProcessorConfig(database_url="", redis_url=REDIS_URL, consumer="t", block_ms=100))
    proc.redis = client
    handled: list[int] = []

    async def handle(entries):  # type: ignore[no-untyped-def]
        handled.extend(int(f[b"device_id"]) for _, f in entries)
    proc.handle = handle  # type: ignore[method-assign]
    await proc.ensure_group()
    task = asyncio.create_task(proc._consume())
    try:
        await client.xadd(STREAM, {"device_id": 1, "record": "{}"})
        for _ in range(50):
            if handled:
                break
            await asyncio.sleep(0.05)
        assert handled == [1]
        await client.delete(STREAM)                       # Redis restarted without persistence / was flushed
        await client.xadd(STREAM, {"device_id": 2, "record": "{}"})   # gateway publishes again: stream, no group
        for _ in range(100):
            if 2 in handled:
                break
            await asyncio.sleep(0.05)
        assert 2 in handled and proc.stats.group_recreated >= 1
        assert (await client.xinfo_groups(STREAM))[0]["name"] in (GROUP, GROUP.encode())
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await client.delete(STREAM)
        await client.aclose()


def test_json_logs_include_structured_fields():
    record = logging.LogRecord("gpsproc", logging.ERROR, __file__, 1, "stream read failed", None, None)
    record.error = "ResponseError: NOGROUP"
    out = json.loads(JsonFormatter().format(record))
    assert out["msg"] == "stream read failed" and out["error"] == "ResponseError: NOGROUP" and out["level"] == "ERROR"
