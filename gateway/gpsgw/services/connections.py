"""Tracks open device connections so a disabled device can be cut off."""
from __future__ import annotations

import asyncio
from collections import defaultdict


class ConnectionTracker:
    def __init__(self) -> None:
        self._by_identifier: dict[str, set[asyncio.StreamWriter]] = defaultdict(set)
        #: Open TCP sockets across every listener of this process (for the global connection limit).
        self.sockets = 0

    def add(self, identifier: str, writer: asyncio.StreamWriter) -> None:
        self._by_identifier[identifier].add(writer)

    def writers(self, identifier: str) -> list[asyncio.StreamWriter]:
        return [w for w in self._by_identifier.get(identifier, ()) if not w.is_closing()]

    def remove(self, identifier: str, writer: asyncio.StreamWriter) -> None:
        conns = self._by_identifier.get(identifier)
        if conns:
            conns.discard(writer)
            if not conns:
                del self._by_identifier[identifier]

    def count(self, identifier: str | None = None) -> int:
        if identifier is not None:
            return len(self._by_identifier.get(identifier, ()))
        return sum(len(v) for v in self._by_identifier.values())

    def close_device(self, identifier: str) -> int:
        """Close every connection of ``identifier``; returns how many were closed."""
        writers = self._by_identifier.pop(identifier, set())
        for w in writers:
            w.close()
        return len(writers)
