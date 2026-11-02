"""Spec A16 simulator scenarios: each is a behaviour for the vehicle and the tracker's network."""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from gpssim.client import Rejected, TcpClient, UdpClient
from gpssim.routes import ROUTES, Route
from gpssim.vehicle import Behaviour, VehicleSim

SCENARIOS = ["normal_day", "overspeed", "geofence_cross", "long_idle", "go_offline", "coverage_gap_backlog",
             "gps_jump", "invalid_fix", "duplicate_resend", "unknown_imei", "malformed_packet"]


@dataclass
class Plan:
    behaviour: Behaviour = field(default_factory=Behaviour)
    route: str = "morogoro_road"
    offline_after: int | None = None          # go_offline: stop for good after this step
    gap: tuple[int, int] | None = None        # coverage gap: (first step, number of steps buffered)
    resend: bool = False
    malformed_at: int | None = None
    unknown: bool = False


def plan_for(scenario: str, route: str, interval_s: float, gap_minutes: float = 120) -> Plan:
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; choose from {', '.join(SCENARIOS)}")
    p = Plan(route=route)
    if scenario == "overspeed":
        p.behaviour.overspeed_leg = 1
    elif scenario == "geofence_cross":
        p.route = "port_airport"   # passes the port and Kurasini depot areas
    elif scenario == "long_idle":
        p.behaviour.long_idle_minutes = 8
    elif scenario == "go_offline":
        p.offline_after = 18
    elif scenario == "coverage_gap_backlog":
        p.gap = (12, max(1, int(gap_minutes * 60 / interval_s)))
    elif scenario == "gps_jump":
        p.behaviour.jump_at_step = 15
    elif scenario == "invalid_fix":
        p.behaviour.invalid_every = 5
    elif scenario == "duplicate_resend":
        p.resend = True
    elif scenario == "unknown_imei":
        p.unknown = True
    elif scenario == "malformed_packet":
        p.malformed_at = 6
    return p


def random_imei(rng: random.Random) -> str:
    body = "35" + "".join(rng.choice("0123456789") for _ in range(12))
    for check in "0123456789":
        digits = body + check
        total = sum((d * 2 - 9 if d * 2 > 9 else d * 2) if i % 2 else d
                    for i, d in enumerate(int(c) for c in reversed(digits)))
        if total % 10 == 0:
            return digits
    raise AssertionError("unreachable")


@dataclass
class RunStats:
    imei: str
    scenario: str
    records_generated: int = 0
    records_acked: int = 0
    packets: int = 0
    resends: int = 0
    reconnects: int = 0
    rejected: bool = False
    commands_answered: int = 0
    malformed_closed: bool = False
    max_backlog: int = 0

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


async def run_device(host: str, port: int, imei: str, scenario: str, *, steps: int = 60, interval_s: float = 10.0,
                     speedup: float = 0.0, start: datetime | None = None, transport: str = "tcp",
                     route: str = "morogoro_road", seed: int = 1, batch: int = 20,
                     gap_minutes: float = 120, hold_seconds: float = 0.0, protocol: str = "teltonika",
                     token: str = "", path: str = "/fleet-json") -> RunStats:
    """Drive one tracker. ``speedup`` 0 sends as fast as the server ACKs (backfill); otherwise sleeps
    interval/speedup between records. Unacknowledged records stay queued and are resent, like device flash."""
    plan = plan_for(scenario, route, interval_s, gap_minutes)
    rng = random.Random(seed)
    if plan.unknown:
        imei = random_imei(rng)
    stats = RunStats(imei, scenario)
    start = start or datetime.now(UTC) - timedelta(seconds=steps * interval_s)
    sim = VehicleSim(Route.named(plan.route, reverse=seed % 2 == 0), start, interval_s, plan.behaviour, seed)
    client: Any
    if protocol == "gt06":
        from gpssim.other_protocols import Gt06Client
        client = Gt06Client(host, port, imei)
    elif protocol == "http":
        from gpssim.other_protocols import HttpJsonClient
        client = HttpJsonClient(host, port, imei, token, path)
    else:
        client = (UdpClient if transport == "udp" else TcpClient)(host, port, imei)
    queue: list[Any] = []
    for step in range(1, steps + 1):
        if plan.offline_after and step > plan.offline_after:
            break
        queue.append(sim.tick())
        stats.records_generated += 1
        in_gap = plan.gap is not None and plan.gap[0] <= step < plan.gap[0] + plan.gap[1]
        stats.max_backlog = max(stats.max_backlog, len(queue))
        if in_gap:
            if client.connected and transport == "tcp":
                await client.close()
            continue
        try:
            if not client.connected or (transport == "udp" or protocol == "http") and step == 1:
                await client.connect()
            if plan.malformed_at == step and isinstance(client, TcpClient):
                try:
                    await client.send_raw(b"\x01\x02\x03\x04not-a-teltonika-packet")
                except (asyncio.IncompleteReadError, ConnectionError, TimeoutError):
                    stats.malformed_closed = True
                await client.close()
                stats.reconnects += 1
                await client.connect()
            while queue:
                chunk = queue[:batch]
                acked = await client.send(chunk)
                stats.packets += 1
                if acked != len(chunk):
                    raise ConnectionError(f"ACK {acked} for {len(chunk)} records")
                if plan.resend and isinstance(client, TcpClient):
                    stats.resends += 1
                    await client.send(chunk)   # the device missed the ACK and sends the same packet again
                    stats.packets += 1
                del queue[:len(chunk)]
                stats.records_acked += acked
        except Rejected:
            stats.rejected = True
            break
        except (ConnectionError, OSError, TimeoutError, asyncio.IncompleteReadError):
            await client.close()
            stats.reconnects += 1
        if speedup > 0:
            if isinstance(client, TcpClient):
                await client.idle(interval_s / speedup)     # stay responsive to commands between records
            else:
                await asyncio.sleep(interval_s / speedup)
    if hold_seconds and isinstance(client, TcpClient) and client.connected and not stats.rejected:
        await client.idle(hold_seconds)                     # keep the link open (FMC920 "Open Link Timeout")
    if isinstance(client, TcpClient):
        stats.commands_answered = len(client.commands_received)
    await client.close()
    return stats


DEMO_FLEET = [("356307042441013", "morogoro_road"), ("352093086403655", "port_airport"),
              ("353201234567896", "bagamoyo_road")]


async def run_fleet(host: str, port: int, scenario: str, imeis: list[str] | None = None, **kw: Any) -> list[RunStats]:
    pairs = [(i, r) for i, r in DEMO_FLEET] if not imeis else \
        [(imei, list(ROUTES)[n % len(ROUTES)]) for n, imei in enumerate(imeis)]
    return list(await asyncio.gather(*(run_device(host, port, imei, scenario, route=route, seed=n + 1, **kw)
                                       for n, (imei, route) in enumerate(pairs))))
