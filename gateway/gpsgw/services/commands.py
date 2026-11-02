"""Codec 12 command delivery (spec A6 Commands, A13).

Commands can only be delivered while the tracker's TCP connection is open. PENDING commands are sent when the
tracker connects, or at once when the web app queues one for a connected tracker (Redis ``commands`` channel).
One command is outstanding per tracker at a time; its response (or a timeout) completes it.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from gpsgw.protocols.teltonika.codec12 import encode_command
from gpsgw.services.connections import ConnectionTracker

log = logging.getLogger("gpsgw.commands")


class CommandService:
    def __init__(self, pool: Any, tracker: ConnectionTracker, response_timeout: float = 60.0) -> None:
        self.pool = pool
        self.tracker = tracker
        self.response_timeout = timedelta(seconds=response_timeout)
        self._locks: dict[str, asyncio.Lock] = {}
        self.sent = self.acknowledged = self.timed_out = 0

    def _lock(self, identifier: str) -> asyncio.Lock:
        return self._locks.setdefault(identifier, asyncio.Lock())

    async def deliver(self, identifier: str) -> bool:
        """Send the oldest PENDING command if the tracker is connected and nothing is outstanding.

        The command is committed as SENT *before* it is written to the socket: a fast tracker can answer within
        milliseconds, and its response must find an outstanding command (otherwise it would be discarded).
        """
        writers = self.tracker.writers(identifier)
        if not writers:
            return False
        async with self._lock(identifier):
            async with self.pool.acquire() as conn, conn.transaction():
                outstanding = await conn.fetchval(
                    "SELECT 1 FROM commands c JOIN devices d ON d.id = c.device_id WHERE d.identifier = $1 "
                    "AND c.status = 'SENT' LIMIT 1", identifier)
                if outstanding:
                    return False
                cmd = await conn.fetchrow(
                    "SELECT c.id, c.command_text FROM commands c JOIN devices d ON d.id = c.device_id "
                    "WHERE d.identifier = $1 AND c.status = 'PENDING' AND c.expires_at > now() "
                    "ORDER BY c.created_at LIMIT 1 FOR UPDATE OF c SKIP LOCKED", identifier)
                if cmd is None:
                    return False
                try:
                    packet = encode_command(cmd["command_text"])
                except Exception as exc:
                    await conn.execute("UPDATE commands SET status = 'FAILED', completed_at = now(), error = $2 "
                                       "WHERE id = $1", cmd["id"], f"cannot encode: {exc}"[:300])
                    return False
                await conn.execute("UPDATE commands SET status = 'SENT', sent_at = now() WHERE id = $1", cmd["id"])
            try:                                   # committed: a response can now be matched
                writer = writers[-1]               # the newest connection
                writer.write(packet)
                await writer.drain()
            except (ConnectionError, OSError) as exc:
                await self.pool.execute("UPDATE commands SET status = 'FAILED', completed_at = now(), error = $2 "
                                        "WHERE id = $1", cmd["id"], f"connection lost while sending: {exc}"[:300])
                return False
            self.sent += 1
            log.info("command sent", extra={"command_id": cmd["id"]})
            return True

    async def on_response(self, identifier: str, text: str) -> int | None:
        """Store a device's response against its outstanding command, then send the next one."""
        async with self.pool.acquire() as conn, conn.transaction():
            cmd_id = await conn.fetchval(
                "SELECT c.id FROM commands c JOIN devices d ON d.id = c.device_id WHERE d.identifier = $1 "
                "AND c.status = 'SENT' ORDER BY c.sent_at LIMIT 1 FOR UPDATE OF c", identifier)
            if cmd_id is None:
                log.warning("command response without an outstanding command")
                return None
            now = datetime.now(UTC)
            await conn.execute("INSERT INTO command_responses (command_id, response_text, received_at) "
                               "VALUES ($1, $2, $3)", cmd_id, text[:10000], now)
            await conn.execute("UPDATE commands SET status = 'ACKNOWLEDGED', completed_at = $2 WHERE id = $1",
                               cmd_id, now)
        self.acknowledged += 1
        await self.deliver(identifier)
        return int(cmd_id)

    async def expire(self) -> int:
        """SENT without a response -> TIMEOUT; PENDING past its expiry (never connected) -> TIMEOUT."""
        cutoff = datetime.now(UTC) - self.response_timeout
        a = await self.pool.execute(
            "UPDATE commands SET status = 'TIMEOUT', completed_at = now(), error = 'no response from the tracker' "
            "WHERE status = 'SENT' AND sent_at < $1", cutoff)
        b = await self.pool.execute(
            "UPDATE commands SET status = 'TIMEOUT', completed_at = now(), "
            "error = 'not delivered: the tracker did not connect before the command expired' "
            "WHERE status = 'PENDING' AND expires_at <= now()")
        n = int(a.rsplit(" ", 1)[-1]) + int(b.rsplit(" ", 1)[-1])
        self.timed_out += n
        return n

    async def run(self, interval: float = 10.0) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await self.expire()
            except Exception as exc:
                log.warning("command expiry failed", extra={"error": type(exc).__name__})
