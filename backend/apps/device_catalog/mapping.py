"""Apply an IO mapping profile to raw IO values (for the editor's live preview, spec A7).

Mirrors the field/sensor/event logic of ``gateway/gpsgw/normalization/io_mapper.normalize``; the contract test
``test_preview_matches_gateway`` checks both produce the same result for the same input.
"""
from __future__ import annotations

from typing import Any

from apps.device_catalog.io_schema import MAPPABLE_FIELDS

BOOL_FIELDS = {"ignition", "motion"}
INT_FIELDS = {"gsm_signal"}


def apply_mappings(mappings: list[dict[str, Any]], io: dict[str, Any]) -> dict[str, Any]:
    """Return {"fields": {...}, "sensors": {...}, "events": [...], "unmapped": {...}} for raw IO {id: value}."""
    fields: dict[str, Any] = {}
    sensors: dict[str, Any] = {}
    events: list[str] = []
    mapped_ids = set()
    for m in mappings:
        key = str(m["io_id"])
        if key not in io:
            continue
        value = io[key]
        if not isinstance(value, int | float) or isinstance(value, bool):
            continue  # variable-length (hex) values are never mapped
        mapped_ids.add(key)
        if m["kind"] == "event":
            event_map = m.get("event_map", {})
            name = event_map.get(str(value)) or (event_map.get("*") if value else None)
            if name:
                events.append(name)
            continue
        scaled = value * m.get("scale", 1.0) + m.get("offset", 0.0)
        if m["kind"] == "field" and m["target"] in MAPPABLE_FIELDS:
            target = m["target"]
            fields[target] = bool(value) if target in BOOL_FIELDS else int(scaled) if target in INT_FIELDS \
                else round(scaled, 6)
        elif m["kind"] == "sensor":
            sensors[m["target"]] = round(scaled, 6)
    return {"fields": fields, "sensors": sensors, "events": events,
            "unmapped": {k: v for k, v in io.items() if k not in mapped_ids}}
