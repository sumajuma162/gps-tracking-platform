"""TCP connection limits (spec A4.11)."""
from __future__ import annotations

import asyncio

from gpsgw.protocols.gt06.decoder import Gt06Decoder


async def test_connection_limits_per_ip_and_global():
    """Spec A4.11: a per-source-IP cap and a global cap across all listeners; refusals are counted."""
    from gpsgw.listeners.tcp import TcpLimits, TcpListener
    from gpsgw.services.connections import ConnectionTracker
    from gpsgw.services.memory import MemoryInbox, MemoryPersistence, MemoryPublisher, MemoryRegistry
    from gpsgw.services.pipeline import IngestPipeline

    pipeline = IngestPipeline(MemoryPersistence(), MemoryPublisher())
    tracker = ConnectionTracker()
    a = TcpListener(Gt06Decoder(), MemoryRegistry(), pipeline, MemoryInbox(), tracker=tracker,
                    limits=TcpLimits(max_connections_per_ip=2, max_connections=3, handshake_timeout=5))
    b = TcpListener(Gt06Decoder(), MemoryRegistry(), pipeline, MemoryInbox(), tracker=tracker,
                    limits=TcpLimits(max_connections_per_ip=10, max_connections=3, handshake_timeout=5))
    pa, pb = await a.start("127.0.0.1"), await b.start("127.0.0.1")
    held = []
    try:
        async def open_(port: int) -> bool:
            r, w = await asyncio.open_connection("127.0.0.1", port)
            held.append(w)
            try:
                return await asyncio.wait_for(r.read(1), 0.3) != b""
            except TimeoutError:
                return True                      # still open: accepted
        assert [await open_(pa) for _ in range(3)] == [True, True, False]       # per-IP cap on listener a
        assert [await open_(pb) for _ in range(2)] == [True, False]             # global cap: 2 + 1 = 3
        assert tracker.sockets == 3
        text = pipeline.metrics.render().decode()
        assert 'code="connection_limit"' in text
    finally:
        for w in held:
            w.close()
        await a.stop()
        await b.stop()
