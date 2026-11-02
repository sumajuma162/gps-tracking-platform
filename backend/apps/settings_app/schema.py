"""The settings every organization has (spec A13 "Settings", A10 default thresholds).

One table of keys, defaults and bounds drives validation, the API, the web form and the processor (which reads the
stored values). Platform defaults override these built-in defaults; an organization's values override the platform's.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Setting:
    key: str
    default: Any
    group: str
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    integer: bool = False

    def clean(self, value: Any) -> Any:
        if self.choices:
            if value not in self.choices:
                raise ValueError(f"must be one of {', '.join(self.choices)}")
            return value
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("must be a number")
        if self.integer and float(value) != int(value):
            raise ValueError("must be a whole number")
        if self.minimum is not None and value < self.minimum:
            raise ValueError(f"must be at least {self.minimum:g}")
        if self.maximum is not None and value > self.maximum:
            raise ValueError(f"must be at most {self.maximum:g}")
        return int(value) if self.integer else float(value)


S = Setting
SETTINGS: dict[str, Setting] = {s.key: s for s in [
    # Status (A10 offline / GPS-loss detection)
    S("offline_minutes", 5, "status", 1, 1440, integer=True),
    S("no_gps_minutes", 10, "status", 1, 1440, integer=True),
    S("moving_speed_kmh", 5.0, "status", 1, 50),
    # Trips, stops and idling
    S("trip_start_kmh", 5.0, "trips", 1, 50),
    S("min_trip_km", 0.5, "trips", 0, 50),
    S("min_trip_minutes", 2, "trips", 0, 120, integer=True),
    S("stop_speed_kmh", 3.0, "trips", 0, 30),
    S("min_stop_minutes", 3, "trips", 1, 240, integer=True),
    S("idle_minutes", 5, "trips", 1, 240, integer=True),
    S("movement_radius_m", 30.0, "trips", 0, 500),
    S("long_stop_minutes", 120, "trips", 5, 10080, integer=True),
    # Overspeed
    S("overspeed_warning_kmh", 80.0, "speed", 10, 250),
    S("overspeed_critical_kmh", 100.0, "speed", 10, 300),
    S("overspeed_min_seconds", 15, "speed", 0, 600, integer=True),
    # Geofences
    S("geofence_hysteresis_points", 2, "geofences", 1, 20, integer=True),
    S("geofence_hysteresis_seconds", 20, "geofences", 0, 600, integer=True),
    # Map
    S("map_center_lat", -6.7924, "map", -90, 90),
    S("map_center_lon", 39.2083, "map", -180, 180),
    S("map_zoom", 12, "map", 2, 19, integer=True),
    # Data and notifications
    S("retention_days", 730, "data", 30, 3650, integer=True),
    S("email_min_severity", "critical", "notifications", choices=("info", "warning", "critical")),
]}

DEFAULTS: dict[str, Any] = {k: s.default for k, s in SETTINGS.items()}


def clean_values(values: Any, *, allow_null: bool) -> dict[str, Any]:
    """Validate a partial update. ``None`` (when allowed) removes an override. Raises ``ValueError`` with a
    ``{key: message}`` dict as its only argument."""
    if not isinstance(values, dict):
        raise ValueError({"values": "must be an object"})
    out: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for key, value in values.items():
        setting = SETTINGS.get(key)
        if setting is None:
            errors[key] = "unknown setting"
        elif value is None and allow_null:
            out[key] = None
        else:
            try:
                out[key] = setting.clean(value)
            except ValueError as exc:
                errors[key] = str(exc)
    if errors:
        raise ValueError(errors)
    return out


def check_consistency(values: dict[str, Any]) -> dict[str, str]:
    """Rules across keys, applied to the effective values."""
    errors = {}
    if values["overspeed_critical_kmh"] < values["overspeed_warning_kmh"]:
        errors["overspeed_critical_kmh"] = "must be at least the warning speed"
    if values["no_gps_minutes"] < values["offline_minutes"]:
        errors["no_gps_minutes"] = "must be at least the offline timeout"
    return errors
