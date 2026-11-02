"""Data-driven IO mapping: protocol IO IDs -> normalized fields, sensors, events.

Profiles are plain data (seeded JSON, editable in the UI, versioned in the DB).
Formulas are declarative scale/offset only; no user-supplied code is executed.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from importlib import resources
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from gpsgw.normalization.normalized_location import NormalizedLocation
from gpsgw.normalization.validators import ValidationConfig, check_record, gps_fix_is_valid
from gpsgw.protocols.base import RawRecord

#: Normalized fields an IO value may populate.
MAPPABLE_FIELDS = frozenset({
    "ignition", "motion", "odometer_km", "engine_hours", "external_voltage",
    "battery_voltage", "battery_level", "gsm_signal", "hdop",
})
BOOL_FIELDS = frozenset({"ignition", "motion"})
INT_FIELDS = frozenset({"gsm_signal"})


class IoMapping(BaseModel):
    io_id: int
    kind: Literal["field", "sensor", "event"]
    target: str
    scale: float = 1.0
    offset: float = 0.0
    unit: str | None = None
    #: For events: value -> event name. "*" means any non-zero value.
    event_map: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> IoMapping:
        if self.kind == "field" and self.target not in MAPPABLE_FIELDS:
            raise ValueError(f"unknown normalized field: {self.target}")
        if self.kind == "event" and not self.event_map:
            raise ValueError("event mapping needs event_map")
        return self


class IoMappingProfile(BaseModel):
    name: str
    version: int = 1
    verified: bool = False
    mappings: list[IoMapping]

    @model_validator(mode="after")
    def _unique(self) -> IoMappingProfile:
        ids = [m.io_id for m in self.mappings]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate io_id in profile")
        return self

    @classmethod
    def load_builtin(cls, name: str) -> IoMappingProfile:
        text = resources.files("gpsgw.normalization.profiles").joinpath(f"{name}.json").read_text()
        return cls.model_validate_json(text)


def _io_json(value: int | bytes) -> int | str:
    return value.hex() if isinstance(value, bytes) else value


def record_hash(identifier: str, raw: RawRecord) -> str:
    payload = json.dumps(
        [identifier, raw.device_time.isoformat(), raw.latitude, raw.longitude,
         raw.speed_kmh, raw.event_io_id, sorted((k, _io_json(v)) for k, v in raw.io.items())],
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def normalize(
    raw: RawRecord,
    *,
    identifier: str,
    protocol: str,
    profile: IoMappingProfile | None,
    server_time: datetime,
    device_id: int | None = None,
    validation: ValidationConfig | None = None,
) -> NormalizedLocation:
    """Validate a raw record and map it to ``NormalizedLocation``.

    Raises ``InvalidRecord`` when the record must be rejected.
    """
    check_record(raw, server_time, validation or ValidationConfig())
    fields: dict[str, Any] = {}
    sensors: dict[str, float | int | bool] = {}
    events: list[str] = []
    for m in (profile.mappings if profile else []):
        if m.io_id not in raw.io:
            continue
        value = raw.io[m.io_id]
        if isinstance(value, bytes):
            continue  # variable-length values stay in raw_metadata only
        if m.kind == "event":
            name = m.event_map.get(str(value)) or (m.event_map.get("*") if value else None)
            if name:
                events.append(name)
            continue
        scaled = value * m.scale + m.offset
        if m.kind == "field":
            if m.target in BOOL_FIELDS:
                fields[m.target] = bool(value)
            elif m.target in INT_FIELDS:
                fields[m.target] = int(scaled)
            else:
                fields[m.target] = round(scaled, 6)
        else:
            sensors[m.target] = round(scaled, 6)
    # Generic formats (spec A7) fill normalized fields and sensors directly from their field mapping.
    for name, value in (raw.extra.get("fields") or {}).items():
        if name in MAPPABLE_FIELDS:
            fields.setdefault(name, value)
    for name, value in (raw.extra.get("sensors") or {}).items():
        sensors.setdefault(str(name), value)

    return NormalizedLocation(
        device_id=device_id,
        device_identifier=identifier,
        protocol=protocol,
        device_time=raw.device_time,
        server_time=server_time,
        latitude=raw.latitude,
        longitude=raw.longitude,
        altitude=raw.altitude,
        speed_kmh=raw.speed_kmh,
        heading=raw.heading,
        satellites=raw.satellites,
        gps_valid=gps_fix_is_valid(raw),
        event_code=raw.event_io_id,
        events=events,
        sensors=sensors,
        priority=raw.priority,
        raw_metadata={"io": {str(k): _io_json(v) for k, v in sorted(raw.io.items())}, **raw.extra},
        record_hash=record_hash(identifier, raw),
        **fields,
    )
