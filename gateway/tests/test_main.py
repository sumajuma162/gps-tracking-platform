"""Gateway assembly: storage selection, memory dev mode, health and metrics endpoints."""
import asyncio
import json

import pytest

from gpsgw import main as gateway_main
from gpsgw.app import Gateway, StartupError
from gpsgw.config import GatewayConfig
from gpsgw.protocols.teltonika.encoder import encode_imei


def cfg(**kw):
    base = dict(teltonika_tcp_port=0, teltonika_udp_port=0, health_port=0, storage="memory",
                allow_memory_storage=True, dev_devices=("356307042441013",), listen_host="127.0.0.1")
    base.update(kw)
    return GatewayConfig(**base)


async def test_memory_storage_requires_explicit_opt_in():
    with pytest.raises(StartupError, match="memory"):
        await Gateway(cfg(allow_memory_storage=False)).start()


async def test_postgres_requires_database_url():
    with pytest.raises(StartupError, match="DATABASE_URL"):
        await Gateway(cfg(storage="postgres")).start()


async def test_unknown_storage_rejected():
    with pytest.raises(StartupError):
        await Gateway(cfg(storage="sqlite")).start()


async def get(port: int, path: str) -> tuple[bytes, bytes]:
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(f"GET {path} HTTP/1.1\r\n\r\n".encode())
    raw = await r.read()
    w.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    return head, body


async def test_memory_gateway_health_and_metrics():
    gw = Gateway(cfg())
    await gw.start()
    try:
        hport = gw.health.sockets[0].getsockname()[1]
        head, body = await get(hport, "/health")
        assert head.startswith(b"HTTP/1.1 200") and json.loads(body)["storage"] == "memory"
        head, body = await get(hport, "/metrics")
        assert head.startswith(b"HTTP/1.1 200") and b"# TYPE gpsgw_records_total counter" in body
        head, _ = await get(hport, "/nope")
        assert head.startswith(b"HTTP/1.1 404")
    finally:
        await gw.stop()


async def test_memory_gateway_accepts_dev_device():
    from gpsgw.listeners.tcp import TcpListener
    from gpsgw.protocols.teltonika.decoder import TeltonikaDecoder
    from gpsgw.services.memory import MemoryInbox, MemoryPersistence, MemoryPublisher, MemoryRegistry
    from gpsgw.services.pipeline import IngestPipeline
    from gpsgw.services.ports import DeviceInfo

    registry = MemoryRegistry([DeviceInfo(1, "356307042441013", "teltonika", 0, None, None)])
    pipeline = IngestPipeline(MemoryPersistence(), MemoryPublisher())
    tcp = TcpListener(TeltonikaDecoder(), registry, pipeline, MemoryInbox())
    port = await tcp.start("127.0.0.1")
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(encode_imei("356307042441013"))
    assert await r.readexactly(1) == b"\x01"
    w.close()
    await tcp.stop()


async def test_catalog_change_event_clears_the_whole_registry_cache():
    gw = Gateway(cfg())

    class Spy:
        calls: list[object] = []

        def invalidate(self, identifier: object = None) -> None:
            self.calls.append(identifier)

    gw.registry = Spy()
    await gw._on_device_event({"action": "catalog_changed", "identifier": "*", "active": True})
    await gw._on_device_event({"action": "assigned", "identifier": "356307042441013", "active": True})
    await gw._on_device_event({"action": "noop"})
    assert Spy.calls == [None, "356307042441013"]


async def test_process_run_starts_and_stops_gateway(monkeypatch):
    calls: list[str] = []

    class FakeGateway:
        async def start(self) -> None:
            calls.append("start")

        def snapshot(self) -> dict[str, object]:
            return {"storage": "memory"}

        async def stop(self) -> None:
            calls.append("stop")

    def signal_handler(self, sig, callback, *args):  # type: ignore[no-untyped-def]
        callback(*args)

    monkeypatch.setattr(gateway_main, "Gateway", lambda _cfg: FakeGateway())
    monkeypatch.setattr(asyncio.BaseEventLoop, "add_signal_handler", signal_handler)

    await gateway_main.run(cfg())

    assert calls == ["start", "stop"]


def test_main_exits_cleanly_on_startup_error(monkeypatch):
    from gpsgw import jsonlog

    async def fail_startup(_cfg):  # type: ignore[no-untyped-def]
        raise StartupError("test startup failure")

    monkeypatch.setattr(jsonlog, "configure", lambda: None)
    monkeypatch.setattr(gateway_main, "GatewayConfig", type("Config", (), {"from_env": staticmethod(cfg)}))
    monkeypatch.setattr(gateway_main, "run", fail_startup)

    with pytest.raises(SystemExit) as exit_error:
        gateway_main.main()

    assert exit_error.value.code == 2
