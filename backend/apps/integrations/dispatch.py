"""Webhook fan-out and delivery (spec A13): HMAC-SHA256 signed, retried with back-off, every attempt logged.

Signature header: ``X-GPS-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<raw body>">`` using the
webhook's secret. Receivers should recompute it and reject old timestamps (replay protection).
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import socket
import time
import urllib.error
import urllib.request
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.integrations.models import Webhook, WebhookDelivery, WebhookEvent
from common.rls import rls_bypass

BACKOFF = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2),
           timedelta(hours=6)]
#: A webhook is switched off after this many failed deliveries in a row (an admin can switch it back on).
DISABLE_AFTER = 50
USER_AGENT = "GPS-Tracking-Platform-Webhooks/1.0"


class UnsafeUrl(ValueError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect could point at an internal address after the URL check; treat it as the final answer."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def check_url(url: str) -> None:
    """Refuse targets that would let a tenant probe the platform's own network (SSRF): only https (http only when
    ``WEBHOOK_ALLOW_HTTP``), and no loopback, private, link-local or reserved addresses unless
    ``WEBHOOK_ALLOW_PRIVATE`` (development and tests)."""
    parts = urlparse(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise UnsafeUrl("Use an https:// URL.")
    if parts.scheme == "http" and not settings.WEBHOOK_ALLOW_HTTP:
        raise UnsafeUrl("Use an https:// URL.")
    if settings.WEBHOOK_ALLOW_PRIVATE:
        return
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise UnsafeUrl("The host name does not resolve.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast \
                or ip.is_unspecified:
            raise UnsafeUrl("The URL points to a private or local network address.")


def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def body_for(delivery: WebhookDelivery) -> bytes:
    ev = delivery.event
    return json.dumps({"id": f"evt_{ev.pk}", "event": ev.event, "organization_id": ev.organization_id,
                       "occurred_at": ev.occurred_at.isoformat(), "data": _enrich(ev.payload)},
                      separators=(",", ":"), default=str).encode()


def _enrich(payload: dict[str, Any]) -> dict[str, Any]:
    """Add the vehicle's registration when the producer only knew its id (the processor writes ids)."""
    out = dict(payload)
    if out.get("vehicle_id") and "vehicle" not in out:
        from apps.fleet.models import Vehicle
        v = Vehicle.unscoped.filter(pk=out["vehicle_id"]).values_list("registration_number", flat=True).first()
        out["vehicle"] = v
    return out


def send(delivery: WebhookDelivery) -> None:
    """One attempt; updates the delivery and its webhook (caller saves inside its transaction)."""
    hook = delivery.webhook
    body = body_for(delivery)
    ts = int(time.time())
    delivery.attempts += 1
    started = time.monotonic()
    status: int | None = None
    try:
        check_url(hook.url)
        req = urllib.request.Request(hook.url, data=body, method="POST", headers={  # noqa: S310 - checked
            "Content-Type": "application/json", "User-Agent": USER_AGENT, "X-GPS-Event": delivery.event.event,
            "X-GPS-Delivery": str(delivery.pk), "X-GPS-Signature": sign(hook.secret, ts, body)})
        with _OPENER.open(req, timeout=settings.WEBHOOK_TIMEOUT) as resp:
            status = resp.status
            text = resp.read(500).decode(errors="replace")
        error = ""
    except urllib.error.HTTPError as exc:
        status, text, error = exc.code, exc.read(500).decode(errors="replace"), f"HTTP {exc.code}"
    except UnsafeUrl as exc:
        text, error = "", str(exc)
    except Exception as exc:  # timeout, refused, TLS...
        text, error = "", f"{type(exc).__name__}: {exc}"[:300]
    delivery.duration_ms = int((time.monotonic() - started) * 1000)
    delivery.response_status, delivery.response_body, delivery.error = status, text[:500], error[:300]
    hook.last_delivery_at = timezone.now()
    if status is not None and 200 <= status < 300:
        delivery.status, delivery.delivered_at, delivery.next_attempt_at = "succeeded", timezone.now(), None
        hook.consecutive_failures, hook.last_status = 0, "succeeded"
    else:
        hook.consecutive_failures += 1
        hook.last_status = "failed"
        retry = delivery.attempts <= len(BACKOFF) and (status is None or status >= 500 or status in (408, 429))
        if retry:
            delivery.next_attempt_at = timezone.now() + BACKOFF[delivery.attempts - 1]
        else:
            delivery.status, delivery.next_attempt_at = "failed", None
        if hook.consecutive_failures >= DISABLE_AFTER:
            hook.is_active = False


def fan_out(limit: int = 500) -> int:
    """Turn new events into one pending delivery per active, subscribed webhook."""
    created = 0
    with transaction.atomic(), rls_bypass():
        events = list(WebhookEvent.unscoped.select_for_update(skip_locked=True)
                      .filter(dispatched_at__isnull=True).order_by("created_at")[:limit])
        hooks: dict[int, list[Webhook]] = {}
        for ev in events:
            if ev.organization_id not in hooks:
                hooks[ev.organization_id] = list(Webhook.unscoped.filter(organization_id=ev.organization_id,
                                                                         is_active=True))
            for hook in hooks[ev.organization_id]:
                if ev.event in hook.events:
                    WebhookDelivery.unscoped.get_or_create(organization_id=ev.organization_id, webhook=hook, event=ev,
                                                           defaults={"next_attempt_at": timezone.now()})
                    created += 1
        WebhookEvent.unscoped.filter(pk__in=[e.pk for e in events]).update(dispatched_at=timezone.now())
    return created


def deliver_due(limit: int = 50) -> dict[str, int]:
    out = {"succeeded": 0, "failed": 0, "retrying": 0}
    with transaction.atomic(), rls_bypass():
        due = list(WebhookDelivery.unscoped.select_for_update(skip_locked=True).select_related("webhook", "event")
                   .filter(status="pending", next_attempt_at__lte=timezone.now(), webhook__is_active=True)
                   .order_by("next_attempt_at")[:limit])
        for d in due:
            send(d)
            d.save()
            d.webhook.save(update_fields=["consecutive_failures", "last_status", "last_delivery_at", "is_active"])
            out["succeeded" if d.status == "succeeded" else "failed" if d.status == "failed" else "retrying"] += 1
    return out
