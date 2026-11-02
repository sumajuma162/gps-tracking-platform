"""Gateway session behaviour over real sockets (spec A4 rules 1-2, A7 inbox)."""
import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from gpsgw.listeners.tcp import TcpLimits, TcpListener
from gpsgw.listeners.udp import UdpListener
from gpsgw.normalization.io_mapper import IoMappingProfile
from gpsgw.protocols.teltonika.decoder import TeltonikaDecoder
from gpsgw.protocols.teltonika.encoder import AvlRecordSpec, encode_imei, encode_tcp_packet, encode_udp_packet
from gpsgw.services.memory import MemoryInbox, MemoryPersistence, MemoryPublisher, MemoryRegistry
from gpsgw.services.pipeline import IngestPipeline
from gpsgw.services.ports import DeviceInfo

IMEI = "356307042441013"
UNKNOWN = "490154203237518"


def device(active=True):
    return DeviceInfo(device_id=1, identifier=IMEI, protocol="teltonika", organization_id=10,
                      vehicle_id=100, profile=IoMappingProfile.load_builtin("teltonika_fmc920"), active=active)


def records(n=3):
    t0 = datetime.now(UTC) - timedelta(minutes=5)
    return [AvlRecordSpec(time=t0 + timedelta(seconds=10 * i), latitude=-6.81 - i * 0.001, longitude=39.28,
                          satellites=10, speed_kmh=40, io={239: (1, 1), 66: (2, 12500)}) for i in range(n)]


@pytest.fixture
async def stack():
    registry, persistence = MemoryRegistry([device()]), MemoryPersistence()
    publisher, inbox = MemoryPublisher(), MemoryInbox()
    pipeline = IngestPipeline(persistence, publisher)
    limits = TcpLimits(handshake_timeout=2, idle_timeout=2)
    tcp = TcpListener(TeltonikaDecoder(), registry, pipeline, inbox, limits=limits)
    port = await tcp.start("127.0.0.1")
    yield dict(registry=registry, persistence=persistence, publisher=publisher, inbox=inbox,
               pipeline=pipeline, tcp=tcp, port=port)
    await tcp.stop()


async def connect(port):
    return await asyncio.open_connection("127.0.0.1", port)


async def test_full_session_ack_after_persist(stack):
    r, w = await connect(stack["port"])
    w.write(encode_imei(IMEI))
    assert await asyncio.wait_for(r.readexactly(1), 2) == b"\x01"
    w.write(encode_tcp_packet(records(3)))
    assert await asyncio.wait_for(r.readexactly(4), 2) == (3).to_bytes(4, "big")
    assert len(stack["persistence"].rows) == 3
    assert len(stack["publisher"].published) == 3
    stored = next(iter(stack["persistence"].rows.values()))
    assert stored.ignition is True and stored.external_voltage == pytest.approx(12.5)
    w.close()


async def test_duplicate_resend_is_acked_but_not_stored_twice(stack):
    r, w = await connect(stack["port"])
    w.write(encode_imei(IMEI))
    await r.readexactly(1)
    pkt = encode_tcp_packet(records(2))
    w.write(pkt)
    await r.readexactly(4)
    w.write(pkt)  # device did not see the ACK and resends
    assert await asyncio.wait_for(r.readexactly(4), 2) == (2).to_bytes(4, "big")
    assert len(stack["persistence"].rows) == 2
    assert stack["pipeline"].stats.duplicates == 2
    w.close()


async def test_no_ack_when_database_fails(stack):
    stack["persistence"].fail = True
    r, w = await connect(stack["port"])
    w.write(encode_imei(IMEI))
    await r.readexactly(1)
    w.write(encode_tcp_packet(records(2)))
    assert await asyncio.wait_for(r.read(10), 2) == b""  # closed with no ACK
    assert stack["persistence"].rows == {}
    assert stack["pipeline"].stats.persist_failures == 1


async def test_unknown_imei_rejected_and_sent_to_inbox(stack):
    r, w = await connect(stack["port"])
    w.write(encode_imei(UNKNOWN))
    assert await asyncio.wait_for(r.readexactly(1), 2) == b"\x00"
    assert await asyncio.wait_for(r.read(10), 2) == b""
    (attempt,) = stack["inbox"].attempts
    assert attempt.identifier == UNKNOWN and attempt.protocol == "teltonika"


async def test_device_registered_while_running_is_accepted(stack):
    """A7: register via UI -> next connection accepted, no restart."""
    r, w = await connect(stack["port"])
    w.write(encode_imei(UNKNOWN))
    assert await r.readexactly(1) == b"\x00"
    stack["registry"].upsert(DeviceInfo(2, UNKNOWN, "teltonika", 10, None, None))
    r, w = await connect(stack["port"])
    w.write(encode_imei(UNKNOWN))
    assert await asyncio.wait_for(r.readexactly(1), 2) == b"\x01"
    w.close()


async def test_disabled_device_connection_closed(stack):
    r, w = await connect(stack["port"])
    w.write(encode_imei(IMEI))
    await r.readexactly(1)
    assert stack["tcp"].tracker.count(IMEI) == 1
    stack["registry"].upsert(device(active=False))
    assert stack["tcp"].tracker.close_device(IMEI) == 1
    assert await asyncio.wait_for(r.read(10), 2) == b""
    r, w = await connect(stack["port"])
    w.write(encode_imei(IMEI))
    assert await asyncio.wait_for(r.readexactly(1), 2) == b"\x00"


async def test_malformed_packet_closes_only_that_connection(stack):
    r1, w1 = await connect(stack["port"])
    w1.write(encode_imei(IMEI))
    await r1.readexactly(1)
    r2, w2 = await connect(stack["port"])
    w2.write(encode_imei(IMEI))
    await r2.readexactly(1)
    w1.write(b"\x01\x02\x03\x04garbage!")
    assert await asyncio.wait_for(r1.read(10), 2) == b""
    w2.write(encode_tcp_packet(records(1)))
    assert await asyncio.wait_for(r2.readexactly(4), 2) == (1).to_bytes(4, "big")
    assert stack["pipeline"].stats.protocol_errors.get("bad_preamble") == 1
    w2.close()


async def test_handshake_timeout(stack):
    r, w = await connect(stack["port"])
    assert await asyncio.wait_for(r.read(10), 5) == b""


async def test_invalid_records_acked_but_not_stored(stack):
    r, w = await connect(stack["port"])
    w.write(encode_imei(IMEI))
    await r.readexactly(1)
    bad = records(2)
    bad[1].time = datetime.now(UTC) + timedelta(days=2)  # future timestamp
    w.write(encode_tcp_packet(bad))
    assert await asyncio.wait_for(r.readexactly(4), 2) == (2).to_bytes(4, "big")
    assert len(stack["persistence"].rows) == 1
    assert stack["pipeline"].stats.rejected == {"future_timestamp": 1}
    w.close()


async def test_udp_listener_ack_and_unknown():
    registry, persistence, inbox = MemoryRegistry([device()]), MemoryPersistence(), MemoryInbox()
    listener = UdpListener(TeltonikaDecoder(), registry, IngestPipeline(persistence, MemoryPublisher()), inbox)
    port = await listener.start("127.0.0.1")
    loop = asyncio.get_running_loop()
    got: asyncio.Queue[bytes] = asyncio.Queue()

    class Client(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):
            got.put_nowait(data)

    transport, _ = await loop.create_datagram_endpoint(Client, remote_addr=("127.0.0.1", port))
    transport.sendto(encode_udp_packet(IMEI, records(2), packet_id=0xCAFE, avl_packet_id=7))
    assert await asyncio.wait_for(got.get(), 2) == bytes.fromhex("0005CAFE010702")
    transport.sendto(encode_udp_packet(UNKNOWN, records(1)))
    await asyncio.sleep(0.2)
    assert got.empty() and inbox.attempts[0].identifier == UNKNOWN
    assert len(persistence.rows) == 2
    transport.close()
    await listener.stop()
