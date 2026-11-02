"""UI-defined formats for simple trackers (spec A7 "Generic configurable protocol").

A ``GenericFormat`` is plain data, defined by a Super Administrator: where each value is in a JSON message (a dotted
path such as ``position.lat`` or ``fixes.0.lat``) or in a delimited line (a 0-based column index), the time format,
the speed unit, the separator and terminator, the authentication method and an optional ACK string. ``parse``
turns one message into ``(identifier, RawRecord)``. No user-supplied code is ever executed: conversions are a fixed
set of units and time formats.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from gpsgw.protocols.base import ProtocolError, RawRecord

FieldRef = str | int
SPEED_FACTORS = {"kmh": 1.0, "knots": 1.852, "mph": 1.609344, "ms": 3.6}
TRUE_WORDS = {"1", "true", "on", "yes", "y", "t"}
FALSE_WORDS = {"0", "false", "off", "no", "n", "f"}
#: Normalized fields a format may fill directly (same names as the IO mapping profile's fields).
DIRECT_FIELDS = ("ignition", "motion", "battery_voltage", "battery_level", "external_voltage", "odometer_km",
                 "engine_hours", "gsm_signal", "hdop")


class ParseError(ProtocolError):
    """A message that doesn't match its format. ``field`` names the offending setting."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__("invalid_message", f"{field}: {message}")
        self.field = field
        self.detail = message


class GenericFormat(BaseModel):
    slug: str = "format"
    kind: Literal["json", "delimited"]
    identifier: FieldRef
    latitude: FieldRef
    longitude: FieldRef
    timestamp: FieldRef | None = None
    #: iso8601 | unix | unix_ms | a strftime pattern such as ``%d%m%y%H%M%S`` (always read as UTC)
    time_format: str = "iso8601"
    speed: FieldRef | None = None
    speed_unit: Literal["kmh", "knots", "mph", "ms"] = "kmh"
    heading: FieldRef | None = None
    altitude: FieldRef | None = None
    satellites: FieldRef | None = None
    valid: FieldRef | None = None
    fields: dict[str, FieldRef] = Field(default_factory=dict, description="Normalized field -> location")
    sensors: dict[str, FieldRef] = Field(default_factory=dict, description="Custom sensor name -> location")
    separator: str = ","
    terminator: str = "\n"
    auth: Literal["token", "identifier"] = "token"
    ack: str | None = None
    #: JSON only: a list of messages in one body is accepted at this path ("" = the body itself may be a list).
    records_path: str | None = None
    max_message_bytes: int = Field(default=8192, ge=64, le=65536)

    @field_validator("fields")
    @classmethod
    def _known_fields(cls, v: dict[str, FieldRef]) -> dict[str, FieldRef]:
        unknown = sorted(set(v) - set(DIRECT_FIELDS))
        if unknown:
            raise ValueError(f"unknown normalized fields: {', '.join(unknown)}")
        return v

    @model_validator(mode="after")
    def _refs_match_kind(self) -> GenericFormat:
        refs = [self.identifier, self.latitude, self.longitude, self.timestamp, self.speed, self.heading,
                self.altitude, self.satellites, self.valid, *self.fields.values(), *self.sensors.values()]
        for ref in refs:
            if ref is None:
                continue
            if self.kind == "delimited" and not (isinstance(ref, int) and ref >= 0):
                raise ValueError("delimited formats use 0-based column numbers")
            if self.kind == "json" and not (isinstance(ref, str) and ref):
                raise ValueError("JSON formats use field paths such as position.lat")
        if self.kind == "delimited" and (not self.separator or not self.terminator):
            raise ValueError("delimited formats need a separator and a line terminator")
        return self


def _get(container: Any, ref: FieldRef) -> Any:
    if isinstance(ref, int):
        return container[ref].strip() if ref < len(container) else None
    node = container
    for part in str(ref).split("."):
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
        if node is None:
            return None
    return node


def _number(name: str, value: Any) -> float:
    if isinstance(value, bool):
        raise ParseError(name, "expected a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ParseError(name, f"expected a number, got {str(value)[:40]!r}") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise ParseError(name, "expected a finite number")
    return number


def _bool(name: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in TRUE_WORDS:
        return True
    if text in FALSE_WORDS:
        return False
    raise ParseError(name, f"expected yes/no, got {text[:40]!r}")


def _time(fmt: GenericFormat, value: Any, server_time: datetime) -> datetime:
    if value is None or value == "":
        if fmt.timestamp is None:
            return server_time
        raise ParseError("timestamp", "missing")
    try:
        if fmt.time_format == "unix":
            return datetime.fromtimestamp(_number("timestamp", value), UTC)
        if fmt.time_format == "unix_ms":
            return datetime.fromtimestamp(_number("timestamp", value) / 1000, UTC)
        if fmt.time_format == "iso8601":
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        else:
            parsed = datetime.strptime(str(value), fmt.time_format)  # noqa: DTZ007 - read as UTC below
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (ValueError, OverflowError, OSError) as exc:
        raise ParseError("timestamp", f"{str(value)[:40]!r} doesn't match {fmt.time_format}") from exc


def parse_record(fmt: GenericFormat, message: Any, server_time: datetime) -> tuple[str, RawRecord]:
    """One message (a dict for JSON / query strings, a list of columns for delimited) -> identifier and record."""
    identifier = _get(message, fmt.identifier)
    if identifier in (None, ""):
        raise ParseError("identifier", "missing")
    identifier = str(identifier).strip()
    if not 1 <= len(identifier) <= 64 or not identifier.replace("-", "").replace("_", "").isalnum():
        raise ParseError("identifier", "use 1-64 letters, digits, - or _")
    lat = _number("latitude", _get(message, fmt.latitude))
    lon = _number("longitude", _get(message, fmt.longitude))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ParseError("latitude", "coordinates out of range")
    raw = RawRecord(device_time=_time(fmt, _get(message, fmt.timestamp) if fmt.timestamp is not None else None,
                                      server_time), latitude=lat, longitude=lon)
    optional = {"speed": fmt.speed, "heading": fmt.heading, "altitude": fmt.altitude, "satellites": fmt.satellites}
    values = {k: _get(message, ref) for k, ref in optional.items() if ref is not None}
    if values.get("speed") not in (None, ""):
        speed = _number("speed", values["speed"]) * SPEED_FACTORS[fmt.speed_unit]
        if speed < 0:
            raise ParseError("speed", "can't be negative")
        raw.speed_kmh = round(speed, 2)
    if values.get("heading") not in (None, ""):
        raw.heading = _number("heading", values["heading"]) % 360
    if values.get("altitude") not in (None, ""):
        raw.altitude = _number("altitude", values["altitude"])
    if values.get("satellites") not in (None, ""):
        raw.satellites = int(_number("satellites", values["satellites"]))
    if fmt.valid is not None and (v := _get(message, fmt.valid)) not in (None, ""):
        raw.gps_valid = _bool("valid", v)
    direct: dict[str, Any] = {}
    for name, ref in fmt.fields.items():
        v = _get(message, ref)
        if v in (None, ""):
            continue
        direct[name] = _bool(name, v) if name in ("ignition", "motion") else (
            int(_number(name, v)) if name == "gsm_signal" else _number(name, v))
    sensors = {name: _number(name, v) for name, ref in fmt.sensors.items()
               if (v := _get(message, ref)) not in (None, "")}
    raw.extra = {"fields": direct, "sensors": sensors}
    return identifier, raw


def json_messages(fmt: GenericFormat, body: bytes) -> list[Any]:
    """Messages in a JSON body: an object, a list of objects, or a list under ``records_path``."""
    if len(body) > fmt.max_message_bytes * 50:
        raise ParseError("body", "too large")
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ParseError("body", "not valid JSON") from exc
    if fmt.records_path:
        data = _get(data, fmt.records_path)
    items = data if isinstance(data, list) else [data]
    if not items or not all(isinstance(i, dict) for i in items):
        raise ParseError("body", "expected a JSON object or a list of objects")
    if len(items) > 500:
        raise ParseError("body", "at most 500 records per request")
    return items


def split_line(fmt: GenericFormat, line: bytes) -> list[str]:
    if len(line) > fmt.max_message_bytes:
        raise ParseError("line", "too long")
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ParseError("line", "not UTF-8 text") from exc
    return text.strip().split(fmt.separator)


def parse_sample(definition: dict[str, Any], sample: str, server_time: datetime | None = None) -> dict[str, Any]:
    """What the UI's test-parse box shows: each normalized record, or the errors (spec A7). ``stage`` tells a broken
    format definition (``format``) from a sample that doesn't match it (``sample``)."""
    from pydantic import ValidationError

    server_time = server_time or datetime.now(UTC)
    try:
        fmt = GenericFormat.model_validate(definition)
    except ValidationError as exc:
        return {"ok": False, "stage": "format",
                "errors": [{"field": ".".join(str(p) for p in e["loc"]) or "format", "message": e["msg"]}
                           for e in exc.errors()]}
    try:
        if fmt.kind == "json":
            messages = json_messages(fmt, sample.encode())
        else:
            lines = [ln for ln in sample.replace("\r\n", "\n").split("\n" if fmt.terminator in ("\n", "\r\n")
                                                                       else fmt.terminator) if ln.strip()]
            messages = [split_line(fmt, ln.encode()) for ln in lines]
        records = []
        for m in messages:
            identifier, raw = parse_record(fmt, m, server_time)
            records.append({"identifier": identifier, "device_time": raw.device_time.isoformat(),
                            "latitude": raw.latitude, "longitude": raw.longitude, "speed_kmh": raw.speed_kmh,
                            "heading": raw.heading, "altitude": raw.altitude, "satellites": raw.satellites,
                            "gps_valid": raw.gps_valid, **raw.extra["fields"], "sensors": raw.extra["sensors"]})
    except ParseError as exc:
        return {"ok": False, "stage": "sample", "errors": [{"field": exc.field, "message": exc.detail}]}
    return {"ok": True, "stage": "sample", "records": records, "ack": fmt.ack}
