"""Prometheus metrics for the web app (spec A15 "Prometheus metrics from backend, gateway and processor").

Request counts and latency per URL route (the route pattern, never the raw path, to bound label cardinality),
WebSocket clients, and platform gauges read at scrape time (devices by status, queues, the positions stream).
Served at ``/metrics`` on the backend container only: Nginx doesn't route it, and ``METRICS_TOKEN`` (Bearer) is
required when set. See docs/MONITORING.md and DECISIONS.md (prometheus_client rather than django-prometheus).
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Gauge, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

REQUESTS = Counter("gps_http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram("gps_http_request_duration_seconds", "HTTP request duration", ["method", "route"],
                    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
WS_CLIENTS = Gauge("gps_websocket_clients", "Open WebSocket connections", ["kind"])


class MetricsMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        started = time.perf_counter()
        response = self.get_response(request)
        match = getattr(request, "resolver_match", None)
        route = (match.route if match is not None else "").replace("^", "").replace("$", "") or "unmatched"
        if route != "metrics":
            LATENCY.labels(request.method, route).observe(time.perf_counter() - started)
            REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        return response


class PlatformCollector(Collector):
    """Gauges computed when Prometheus scrapes (a few indexed queries; failures never break the scrape)."""

    def collect(self) -> Iterator[GaugeMetricFamily]:
        try:
            yield from self._collect()
        except Exception:  # database or Redis down: the health checks report that, metrics just go missing
            return

    def _collect(self) -> Iterator[GaugeMetricFamily]:
        from django.db import connection

        from common.rls import rls_bypass

        devices = GaugeMetricFamily("gps_devices", "Trackers by live status", labels=["status"])
        queues = GaugeMetricFamily("gps_queue_items", "Items waiting in background queues", labels=["queue"])
        from django.db import transaction
        with transaction.atomic(), rls_bypass(), connection.cursor() as cur:
            cur.execute("SELECT status, count(*) FROM device_state GROUP BY status")
            for status, n in cur.fetchall():
                devices.add_metric([status], n)
            for name, sql in (
                    ("notifications", "SELECT count(*) FROM notifications WHERE status = 'queued'"),
                    ("webhook_deliveries", "SELECT count(*) FROM webhook_deliveries WHERE status = 'pending'"),
                    ("report_jobs", "SELECT count(*) FROM report_jobs WHERE status IN ('queued', 'running')"),
                    ("recomputes", "SELECT count(*) FROM recompute_requests WHERE done_at IS NULL")):
                cur.execute(sql)
                queues.add_metric([name], cur.fetchone()[0])
        try:
            import redis
            r = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, socket_timeout=1)
            queues.add_metric(["celery"], r.llen("celery"))
            queues.add_metric(["positions_stream"], r.xlen("positions"))
        except Exception:  # noqa: S110 - Redis down: those two gauges are simply absent from this scrape
            pass
        yield devices
        yield queues


_registered = False


def register_collectors() -> None:
    global _registered
    if not _registered:
        REGISTRY.register(PlatformCollector())
        _registered = True


def metrics_view(request: HttpRequest) -> HttpResponse:
    token = getattr(settings, "METRICS_TOKEN", "")
    if token and request.headers.get("Authorization", "") != f"Bearer {token}":
        return HttpResponse(status=401)
    register_collectors()
    return HttpResponse(generate_latest(REGISTRY), content_type=CONTENT_TYPE_LATEST)


def ws_opened(kind: str) -> None:
    WS_CLIENTS.labels(kind).inc()


def ws_closed(kind: str) -> None:
    WS_CLIENTS.labels(kind).dec()


__all__ = ["MetricsMiddleware", "metrics_view", "ws_closed", "ws_opened", "Any"]
