"""Device registry change events for the gateway's hot reload (spec A7: no restarts).

Published on Redis pub/sub channel ``device_registry`` after the database transaction commits, so the
gateway never sees a change that was rolled back. Delivery is best effort: the gateway also refreshes its
registry every 60 seconds, so a lost message only delays convergence.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from django.conf import settings
from django.db import transaction

log = logging.getLogger("devices.events")
CHANNEL = "device_registry"
LISTENER_CHANNEL = "listener_ports"


def _publish(payload: dict[str, Any], channel: str = CHANNEL) -> None:
    try:
        import redis

        redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2).publish(
            channel, json.dumps(payload))
    except Exception as exc:  # the 60-second registry refresh covers missed messages
        log.warning("device registry event not published", extra={"error": type(exc).__name__})


def device_changed(device: Any, action: str) -> None:
    payload = {"action": action, "device_id": device.pk, "identifier": device.identifier,
               "organization_id": device.organization_id, "active": bool(device.is_active and not device.deleted_at)}
    transaction.on_commit(lambda: _publish(payload))


def listener_ports_changed() -> None:
    """Ask the gateway to reconcile its listeners now (it also re-reads them every 10 seconds)."""
    transaction.on_commit(lambda: _publish({"action": "reload"}, LISTENER_CHANNEL))


def catalog_changed() -> None:
    """A device model or IO profile changed: the gateway drops its whole registry cache so the change applies
    to the next packet of every affected tracker (spec A7)."""
    transaction.on_commit(lambda: _publish({"action": "catalog_changed", "identifier": "*", "active": True}))
