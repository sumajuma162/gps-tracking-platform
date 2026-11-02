"""Per-organization thresholds (spec A10 "configurable per organization"), read from the web app's settings tables.

Stored values are overrides: platform defaults (``platform_settings``) then the organization's own
(``organization_settings``). Anything not stored keeps the processor's configured value (environment), so a fresh
installation behaves exactly as before. Cached per organization and dropped when the web app announces a change.
"""
from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import timedelta
from typing import Any

from gpsproc.analysis.engine import Config
from gpsproc.status import Thresholds

SQL = """SELECT coalesce((SELECT "values" FROM platform_settings WHERE id = 1), '{}'::jsonb) AS platform,
                coalesce((SELECT "values" FROM organization_settings WHERE organization_id = $1),
                         '{}'::jsonb) AS org"""


def _json(v: Any) -> dict[str, Any]:
    v = json.loads(v) if isinstance(v, str) else v
    return v if isinstance(v, dict) else {}


def thresholds(base: Thresholds, v: dict[str, Any]) -> Thresholds:
    return Thresholds(
        moving_kmh=float(v.get("moving_speed_kmh", base.moving_kmh)),
        offline_after=timedelta(minutes=float(v["offline_minutes"])) if "offline_minutes" in v else base.offline_after,
        no_gps_after=timedelta(minutes=float(v["no_gps_minutes"])) if "no_gps_minutes" in v else base.no_gps_after)


#: settings key -> (Config field, multiplier)
ANALYSIS_KEYS = {
    "trip_start_kmh": ("trip_start_kmh", 1), "stop_speed_kmh": ("stop_kmh", 1), "min_stop_minutes": ("min_stop_s", 60),
    "min_trip_km": ("min_trip_km", 1), "min_trip_minutes": ("min_trip_s", 60), "idle_minutes": ("idle_s", 60),
    "movement_radius_m": ("radius_m", 1), "overspeed_warning_kmh": ("overspeed_warning_kmh", 1),
    "overspeed_critical_kmh": ("overspeed_critical_kmh", 1), "overspeed_min_seconds": ("overspeed_min_s", 1),
    "geofence_hysteresis_points": ("hysteresis_points", 1), "geofence_hysteresis_seconds": ("hysteresis_s", 1),
}


def analysis(base: Config, v: dict[str, Any]) -> Config:
    changes: dict[str, Any] = {}
    for key, (field_name, factor) in ANALYSIS_KEYS.items():
        if key in v:
            current = getattr(base, field_name)
            value = float(v[key]) * factor
            changes[field_name] = int(value) if isinstance(current, int) else value
    return replace(base, **changes) if changes else base


class OrgSettings:
    def __init__(self, ttl: float = 60.0) -> None:
        self.ttl = ttl
        self._cache: dict[int, tuple[float, dict[str, Any]]] = {}

    async def values(self, conn: Any, organization_id: int) -> dict[str, Any]:
        hit = self._cache.get(organization_id)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        row = await conn.fetchrow(SQL, organization_id)
        values = {**_json(row["platform"]), **_json(row["org"])} if row else {}
        self._cache[organization_id] = (time.monotonic() + self.ttl, values)
        return values

    def invalidate(self, organization_id: int | None = None) -> None:
        if organization_id is None:
            self._cache.clear()
        else:
            self._cache.pop(organization_id, None)
