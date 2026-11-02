"""The single record shape every protocol produces (spec A6)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NormalizedLocation(BaseModel):
    model_config = ConfigDict(frozen=True)

    device_id: int | None = None
    device_identifier: str
    protocol: str
    device_time: datetime
    server_time: datetime
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    altitude: float | None = None
    speed_kmh: float | None = Field(default=None, ge=0)
    heading: float | None = None
    satellites: int | None = None
    hdop: float | None = None
    gps_valid: bool
    ignition: bool | None = None
    motion: bool | None = None
    odometer_km: float | None = None
    engine_hours: float | None = None
    external_voltage: float | None = None
    battery_voltage: float | None = None
    battery_level: float | None = None
    gsm_signal: int | None = None
    event_code: int | None = None
    events: list[str] = Field(default_factory=list)
    sensors: dict[str, float | int | bool] = Field(default_factory=dict)
    priority: int | None = None
    #: Older than a record already received from this device (backlog upload, spec A4 rule 4).
    is_late: bool = False
    #: Implied speed from the previous valid fix is impossible (spec A4 rule 7); kept but not routed.
    is_outlier: bool = False
    raw_metadata: dict[str, Any] = Field(default_factory=dict)
    record_hash: str
