"""Geofence geometry upkeep and change notifications."""
from __future__ import annotations

import json
import logging

from django.conf import settings
from django.db import connection, transaction

from apps.geofences.models import Geofence

log = logging.getLogger("geofences")


def sync_area(fence: Geofence) -> None:
    """Keep the PostGIS geography column in step with the editable fields (spec A10: stored as geography)."""
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cur:
        if fence.kind == "circle":
            cur.execute("UPDATE geofences SET area = ST_Buffer(ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)"
                        "::geography WHERE id = %s", [fence.center_lon, fence.center_lat, fence.radius_m, fence.pk])
        else:
            ring = [*fence.polygon, fence.polygon[0]]
            wkt = "POLYGON((" + ", ".join(f"{float(lon):.7f} {float(lat):.7f}" for lat, lon in ring) + "))"
            cur.execute("UPDATE geofences SET area = ST_GeogFromText(%s) WHERE id = %s", [wkt, fence.pk])


def notify_processor(organization_id: int) -> None:
    """The processor caches geofences per organization; tell it to reload (it also refreshes every 60 s)."""
    def send() -> None:
        try:
            import redis
            redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2).publish(
                "geofences", json.dumps({"organization_id": organization_id}))
        except Exception as exc:
            log.warning("geofence change not published", extra={"error": type(exc).__name__})
    transaction.on_commit(send)
