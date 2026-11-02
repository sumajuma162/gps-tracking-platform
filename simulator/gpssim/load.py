"""Load mode (spec A16): thousands of concurrent virtual Teltonika trackers over real TCP, for the A15 targets.

    python -m gpssim.load --devices 2000 --interval 2 --duration 120 --ramp 200

Each virtual device connects, sends its IMEI, then one Codec 8E record every ``interval`` seconds and waits for
the ACK. Reported: devices connected, records ACKed per second, ACK latency percentiles (packet sent -> ACK, which
includes the database write), errors, and optionally the processor's live-update lag (``--processor-health``).
The IMEIs come from ``load_imei`` and must be registered first:
``python manage.py create_load_devices --count 2000`` (backend).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from gpsgw.protocols.teltonika.encoder import AvlRecordSpec, encode_imei, encode_tcp_packet

LOAD_PREFIX = "35999"


def luhn_digit(body: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch) * (2 if i % 2 == 0 else 1)
        total += d - 9 if d > 9 else d
    return str((10 - total % 10) % 10)


def load_imei(index: int) -> str:
    """The n-th load-test IMEI: prefix + 9-digit index + Luhn check digit (15 digits, valid)."""
    body = f"{LOAD_PREFIX}{index:09d}"
    return body + luhn_digit(body)


@dataclass
class Results:
    connected: int = 0
    refused: int = 0
    errors: int = 0
    acked: int = 0
    latencies: list[float] = field(default_factory=list)
    processor_lag_ms: list[float] = field(default_factory=list)
    #: Latency and steady-state throughput are measured after the warm-up (connection ramp) only.
    measure_from: float = 0.0
    measured_acked: int = 0
    measured_seconds: float = 0.0

    def summary(self, duration: float, devices: int, interval: float, per_packet: int = 1) -> dict[str, Any]:
        lat = sorted(self.latencies) or [0.0]

        def pct(p: float) -> float:
            return round(lat[min(len(lat) - 1, int(p * len(lat)))] * 1000, 1)
        return {"devices": devices, "interval_s": interval, "duration_s": round(duration, 1),
                "connected": self.connected, "refused": self.refused, "errors": self.errors,
                "records_acked": self.acked,
                "steady_records_per_s": round(self.measured_acked / self.measured_seconds, 1)
                if self.measured_seconds else None,
                "records_per_packet": per_packet,
                "offered_records_per_s": round(devices * per_packet / interval, 1),
                "ack_latency_ms": {"p50": pct(0.5), "p95": pct(0.95), "p99": pct(0.99), "max": pct(1.0),
                                   "mean": round(statistics.fmean(lat) * 1000, 1)},
                "processor_lag_ms_max": max(self.processor_lag_ms, default=None),
                "processor_lag_ms_median": statistics.median(self.processor_lag_ms) if self.processor_lag_ms
                else None}


async def device(host: str, port: int, imei: str, interval: float, until: float, res: Results,
                 rng: random.Random, per_packet: int = 1) -> None:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 30)
        writer.write(encode_imei(imei))
        if await asyncio.wait_for(reader.readexactly(1), 30) != b"\x01":
            res.refused += 1
            writer.close()
            return
    except (OSError, TimeoutError, asyncio.IncompleteReadError):
        res.errors += 1
        return
    res.connected += 1
    lat, lon = -6.80 + rng.uniform(-0.1, 0.1), 39.25 + rng.uniform(-0.1, 0.1)
    await asyncio.sleep(rng.uniform(0, interval))          # spread the fleet across the interval
    try:
        while time.monotonic() < until:
            recs = []
            for k in range(per_packet):      # Teltonika batches records recorded since the last send
                lat += rng.uniform(-0.0005, 0.0005)
                lon += rng.uniform(-0.0005, 0.0005)
                recs.append(AvlRecordSpec(
                    time=datetime.now(UTC) - timedelta(seconds=interval * (per_packet - 1 - k) / per_packet,
                                                       milliseconds=200),
                    latitude=lat, longitude=lon, satellites=10, speed_kmh=rng.randint(0, 80),
                    heading=rng.randint(0, 359), io={239: (1, 1), 66: (2, 27_500)}))
            started = time.monotonic()
            writer.write(encode_tcp_packet(recs))
            await writer.drain()
            ack = int.from_bytes(await asyncio.wait_for(reader.readexactly(4), 30), "big")
            if started >= res.measure_from:
                res.latencies.append(time.monotonic() - started)
                res.measured_acked += ack
            res.acked += ack
            await asyncio.sleep(max(0.0, interval - (time.monotonic() - started)))
    except (OSError, TimeoutError, asyncio.IncompleteReadError):
        res.errors += 1
    finally:
        writer.close()


async def watch_processor(url: str, until: float, res: Results) -> None:
    while time.monotonic() < until:
        try:
            body = await asyncio.to_thread(lambda: json.loads(urllib.request.urlopen(url, timeout=3).read()))  # noqa: S310
            if time.monotonic() >= res.measure_from:
                res.processor_lag_ms.append(float(body["stats"]["last_lag_ms"]))
        except Exception:  # noqa: S110 - the processor's lag is optional context for the report
            pass
        await asyncio.sleep(2)


async def run(host: str, port: int, devices: int, interval: float, duration: float, ramp: float,
              processor_health: str | None = None, first_index: int = 0, per_packet: int = 1,
              warmup: float = 0.0) -> dict[str, Any]:
    rng = random.Random(7)
    start = time.monotonic()
    until = start + duration
    res = Results(measure_from=start + warmup, measured_seconds=max(duration - warmup, 0.0))
    tasks = []
    if processor_health:
        tasks.append(asyncio.create_task(watch_processor(processor_health, until, res)))
    for i in range(devices):
        tasks.append(asyncio.create_task(device(host, port, load_imei(first_index + i), interval, until, res,
                                                random.Random(rng.random()), per_packet)))
        if ramp > 0:
            await asyncio.sleep(1 / ramp)
    await asyncio.gather(*tasks)
    return res.summary(time.monotonic() - start, devices, interval, per_packet)


def main() -> None:
    p = argparse.ArgumentParser(prog="gpssim.load", description=__doc__.split("\n\n")[0])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5027)
    p.add_argument("--devices", type=int, default=1000)
    p.add_argument("--interval", type=float, default=5.0, help="seconds between records per device")
    p.add_argument("--duration", type=float, default=60.0)
    p.add_argument("--ramp", type=float, default=200.0, help="new connections per second (0 = all at once)")
    p.add_argument("--warmup", type=float, default=0.0, help="seconds excluded from latency/throughput figures")
    p.add_argument("--records-per-packet", type=int, default=1, help="records batched in each packet")
    p.add_argument("--processor-health", help="e.g. http://processor:8082/health")
    a = p.parse_args()
    print(json.dumps(asyncio.run(run(a.host, a.port, a.devices, a.interval, a.duration, a.ramp,
                                     a.processor_health, per_packet=a.records_per_packet, warmup=a.warmup)), indent=2))


if __name__ == "__main__":
    main()
