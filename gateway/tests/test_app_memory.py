"""The assembled gateway in memory mode (development) and its JSON logs."""
from __future__ import annotations

import asyncio
import json
import logging
import socket

import pytest

from gpsgw.app import Gateway, StartupError
from gpsgw.config import GatewayConfig
from gpsgw.jsonlog import JsonFormatter
from gpsgw.protocols.teltonika.encoder import encode_imei


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def config(**kw: object) -> GatewayConfig:
    base = dict(teltonika_tcp_port=free_port(), teltonika_udp_port=0, health_port=free_port(), storage="memory",
                allow_memory_storage=True, dev_devices=("356307042441013",), listen_host="127.0.0.1")
    base.update(kw)
    return GatewayConfig(**base)  # type: ignore[arg-type]


async def test_memory_gateway_serves_trackers_and_health():
    cfg = config()
    gw = Gateway(cfg)
    await gw.start()
    try:
        r, w = await asyncio.open_connection("127.0.0.1", cfg.teltonika_tcp_port)
        w.write(encode_imei("356307042441013"))
        assert await asyncio.wait_for(r.readexactly(1), 2) == b"\x01"
        w.close()
        hr, hw = await asyncio.open_connection("127.0.0.1", cfg.health_port)
        hw.write(b"GET /health HTTP/1.1\r\n\r\n")
        body = json.loads((await asyncio.wait_for(hr.read(), 2)).split(b"\r\n\r\n", 1)[1])
        assert body["status"] == "ok" and set(body["protocols"]) >= {"teltonika", "gt06", "generic_json"}
        await gw._on_device_event({"identifier": ""})                     # ignored
    finally:
        await gw.stop()


@pytest.mark.parametrize("kw,message", [({"allow_memory_storage": False}, "GATEWAY_ALLOW_MEMORY_STORAGE"),
                                        ({"storage": "nope"}, "unknown GATEWAY_STORAGE"),
                                        ({"storage": "postgres"}, "DATABASE_URL")])
async def test_startup_refuses_unsafe_configuration(kw, message):
    with pytest.raises(StartupError, match=message):
        await Gateway(config(**kw)).start()


def test_json_log_lines_keep_extra_fields():
    record = logging.LogRecord("gpsgw", logging.WARNING, __file__, 1, "protocol error", None, None)
    record.code = "crc_mismatch"
    out = json.loads(JsonFormatter().format(record))
    assert out["msg"] == "protocol error" and out["code"] == "crc_mismatch" and out["level"] == "WARNING"
