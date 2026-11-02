"""Validation of IO mapping profiles.

Mirrors ``gateway/gpsgw/normalization/io_mapper.py`` (IoMapping/IoMappingProfile). The contract test
``test_gateway_profile_contract`` validates the gateway's seed profile with these rules so the two
cannot drift apart silently.
"""
from __future__ import annotations

from typing import Any

MAPPABLE_FIELDS = frozenset({
    "ignition", "motion", "odometer_km", "engine_hours", "external_voltage",
    "battery_voltage", "battery_level", "gsm_signal", "hdop",
})
KINDS = frozenset({"field", "sensor", "event"})
NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_")


class ProfileInvalid(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def validate_mappings(mappings: Any) -> list[dict[str, Any]]:
    """Return normalized mappings or raise ``ProfileInvalid`` listing every problem."""
    errors: list[str] = []
    if not isinstance(mappings, list):
        raise ProfileInvalid(["mappings must be a list"])
    if len(mappings) > 500:
        raise ProfileInvalid(["a profile can have at most 500 mappings"])
    seen: set[int] = set()
    out: list[dict[str, Any]] = []
    for i, m in enumerate(mappings):
        where = f"mapping {i + 1}"
        if not isinstance(m, dict):
            errors.append(f"{where}: must be an object")
            continue
        io_id, kind, target = m.get("io_id"), m.get("kind"), m.get("target")
        if not isinstance(io_id, int) or isinstance(io_id, bool) or not 0 <= io_id <= 65535:
            errors.append(f"{where}: io_id must be an integer 0-65535")
        elif io_id in seen:
            errors.append(f"{where}: io_id {io_id} is mapped twice")
        else:
            seen.add(io_id)
        if kind not in KINDS:
            errors.append(f"{where}: kind must be field, sensor or event")
        if not isinstance(target, str) or not target or not set(target) <= NAME_CHARS or len(target) > 40:
            errors.append(f"{where}: target must be lowercase letters, digits or _ (max 40)")
        elif kind == "field" and target not in MAPPABLE_FIELDS:
            errors.append(f"{where}: '{target}' is not a normalized field ({', '.join(sorted(MAPPABLE_FIELDS))})")
        for num in ("scale", "offset"):
            if num in m and (not isinstance(m[num], int | float) or isinstance(m[num], bool)):
                errors.append(f"{where}: {num} must be a number")
        event_map = m.get("event_map", {})
        if kind == "event":
            if not isinstance(event_map, dict) or not event_map:
                errors.append(f"{where}: events need event_map, e.g. {{\"1\": \"towing\"}}")
            elif not all(isinstance(k, str) and isinstance(v, str) and v and set(v) <= NAME_CHARS
                         for k, v in event_map.items()):
                errors.append(f"{where}: event_map keys are values as text, values are event names")
        clean = {"io_id": io_id, "kind": kind, "target": target}
        for key in ("scale", "offset", "unit"):
            if key in m:
                clean[key] = m[key]
        if kind == "event":
            clean["event_map"] = event_map
        out.append(clean)
    if errors:
        raise ProfileInvalid(errors)
    return out
