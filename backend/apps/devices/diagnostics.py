"""Connection-test diagnostics for one tracker (spec A7 step 6).

Everything is derived from what the gateway actually stored, so a stage shows as done only when it really
happened. Must run inside the tracker's tenant context (or ``rls_bypass``).
"""
from __future__ import annotations

from typing import Any

from django.db.models import Q
from django.utils import timezone

from apps.devices.models import Device, DeviceConnection, UnregisteredDeviceAttempt
from apps.tracking.models import DeviceState, Position, RawPacket
from common.rls import rls_bypass

STAGES = ["registered", "connected", "first_packet", "valid_fix", "io_mapped"]
MAPPED_FIELDS = ["ignition", "motion", "external_voltage", "battery_voltage", "gsm_signal", "odometer_km"]


def _distance(a: str, b: str) -> int:
    """Levenshtein distance (identifiers are short)."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def similar_unregistered(identifier: str, since_hours: int = 24) -> list[dict[str, Any]]:
    """Unknown identifiers that differ by one or two characters: almost always a typo in the registration.
    Only near-identical identifiers are shown, never the wider inbox (other customers' traffic)."""
    since = timezone.now() - timezone.timedelta(hours=since_hours)
    with rls_bypass():
        rows = list(UnregisteredDeviceAttempt.objects.filter(last_seen_at__gte=since, dismissed_at__isnull=True)
                    .filter(Q(identifier__startswith=identifier[:6]) | Q(identifier__endswith=identifier[-6:]))
                    .exclude(identifier=identifier)[:50])
    return [{"identifier": r.identifier, "last_seen_at": r.last_seen_at, "attempts": r.attempts}
            for r in rows if _distance(r.identifier, identifier) <= 2][:5]


def diagnose(device: Device, include_raw: bool) -> dict[str, Any]:
    connection = DeviceConnection.objects.filter(device=device).order_by("-connected_at").first()
    state = DeviceState.objects.filter(device=device).first()
    latest = (Position.objects.filter(device_id=device.pk).order_by("-device_time")
              .values("device_time", "server_time", "latitude", "longitude", "speed_kmh", "heading", "satellites",
                      "gps_valid", "is_late", "is_outlier", "events", "sensors", "raw_metadata", *MAPPED_FIELDS)
              .first())
    mapped = {k: latest[k] for k in MAPPED_FIELDS if latest and latest[k] is not None}
    done = {
        "registered": device.is_active,
        "connected": connection is not None,
        "first_packet": latest is not None,
        "valid_fix": bool(state and state.gps_valid),
        "io_mapped": bool(mapped or (latest and (latest["sensors"] or latest["events"]))),
    }
    current = next((s for s in STAGES if not done[s]), None)
    result: dict[str, Any] = {
        "device": {"id": device.pk, "name": device.name, "identifier": device.identifier,
                   "is_active": device.is_active, "model": str(device.device_model)},
        "stages": [{"key": s, "done": done[s]} for s in STAGES],
        "current_stage": current,
        "connection": None if connection is None else {
            "connected_at": connection.connected_at, "disconnected_at": connection.disconnected_at,
            "peer": connection.peer, "transport": connection.transport, "port": connection.port,
            "packets": connection.packets, "records": connection.records, "close_reason": connection.close_reason},
        "latest": None if latest is None else {
            **{k: latest[k] for k in ("device_time", "server_time", "latitude", "longitude", "speed_kmh", "heading",
                                      "satellites", "gps_valid", "is_late", "is_outlier", "events", "sensors")},
            "mapped": mapped, "raw_io": latest["raw_metadata"].get("io", {}),
            "lag_seconds": max(0.0, (latest["server_time"] - latest["device_time"]).total_seconds())},
        "last_packet_at": device.last_packet_at,
        "possible_typos": [] if done["connected"] else similar_unregistered(device.identifier),
        "checked_at": timezone.now(),
    }
    if include_raw:
        result["raw_packets"] = list(RawPacket.objects.filter(device_id=device.pk).order_by("-received_at")
                                     .values("received_at", "direction", "transport", "decode_result",
                                             "payload_hex")[:10])
    return result
