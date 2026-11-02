"""Data forwarder framework — Prepared scope (spec A2, A13).

What exists: the ``DataForwarder`` model, this provider interface and a declarative payload mapping (no code
execution). What doesn't: a delivery loop. To implement one, consume the ``positions`` stream (as the processor
does), call ``build_payload`` per record for each active forwarder, and deliver with retries like
``apps.integrations.dispatch``. Regulator formats must come from the regulator's official specification; none is
assumed here.
"""
from __future__ import annotations

from typing import Any, Protocol

#: Normalized fields a mapping may reference (a subset of ``NormalizedLocation``).
SOURCE_FIELDS = frozenset({"identifier", "device_time", "latitude", "longitude", "speed_kmh", "heading", "altitude",
                           "satellites", "ignition", "odometer_km", "vehicle_registration", "gps_valid"})


class Forwarder(Protocol):
    """A delivery target. ``send`` raises on failure so the caller can retry."""

    def send(self, payloads: list[dict[str, Any]]) -> None: ...


def check_mapping(mapping: dict[str, str]) -> list[str]:
    """Problems with a mapping (empty list when valid)."""
    return [f"{out}: unknown source field {src!r}" for out, src in mapping.items() if src not in SOURCE_FIELDS]


def build_payload(record: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    """Rename normalized fields to the target's names. Only listed fields are sent (data minimisation)."""
    return {out: record.get(src) for out, src in mapping.items() if src in SOURCE_FIELDS}
