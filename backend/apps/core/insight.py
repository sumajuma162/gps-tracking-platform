"""Global search (per organization) and the platform's system health page (spec Phase 9)."""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Any

from django.conf import settings
from django.db import connection
from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions import IsPlatformAdmin
from common.rls import rls_bypass
from common.viewsets import TenantViewSet

LIMIT = 5


class SearchViewSet(TenantViewSet):
    """Search the organization: vehicles, trackers, drivers, geofences and users, each only with permission."""

    required_permissions = {"list": None}

    @extend_schema(parameters=[OpenApiParameter("q", str, required=True)],
                   responses=inline_serializer("SearchResults", {"results": serializers.DictField()}))
    def list(self, request: Request) -> Response:
        from apps.devices.models import Device
        from apps.fleet.models import Driver, Vehicle
        from apps.geofences.models import Geofence
        from apps.organizations.models import OrganizationMembership

        q = (request.query_params.get("q") or "").strip()
        if len(q) < 2:
            return Response({"results": {}})
        perms = self.tenant.permissions
        out: dict[str, list[dict[str, Any]]] = {}
        if "vehicles.view" in perms:
            out["vehicles"] = [{"id": v.pk, "label": v.registration_number, "detail": f"{v.make} {v.model}".strip()}
                               for v in Vehicle.objects.filter(deleted_at__isnull=True).filter(
                                   Q(registration_number__icontains=q) | Q(vin__icontains=q)
                                   | Q(make__icontains=q))[:LIMIT]]
        if "devices.view" in perms:
            out["devices"] = [{"id": d.pk, "label": d.name, "detail": d.identifier}
                              for d in Device.objects.filter(deleted_at__isnull=True).filter(
                                  Q(name__icontains=q) | Q(identifier__icontains=q))[:LIMIT]]
        if "drivers.view" in perms:
            out["drivers"] = [{"id": d.pk, "label": d.full_name, "detail": d.phone or ""}
                              for d in Driver.objects.filter(deleted_at__isnull=True).filter(
                                  Q(first_name__icontains=q) | Q(last_name__icontains=q)
                                  | Q(phone__icontains=q))[:LIMIT]]
        if "geofences.view" in perms:
            out["geofences"] = [{"id": g.pk, "label": g.name, "detail": g.kind}
                                for g in Geofence.objects.filter(deleted_at__isnull=True, name__icontains=q)[:LIMIT]]
        if "users.view" in perms:
            out["users"] = [{"id": m.user_id, "label": m.user.full_name or m.user.email, "detail": m.user.email}
                            for m in OrganizationMembership.objects.select_related("user").filter(
                                Q(user__email__icontains=q) | Q(user__full_name__icontains=q))[:LIMIT]]
        return Response({"results": {k: v for k, v in out.items() if v}})


def _probe(url: str) -> dict[str, Any]:
    if not url:
        return {"status": "not_configured"}
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310 (operator-configured internal URL)
            body = json.loads(resp.read().decode() or "{}")
        return {"status": body.get("status", "ok"), "latency_ms": round((time.perf_counter() - started) * 1000),
                "detail": {k: v for k, v in body.items() if k != "status"}}
    except Exception as exc:
        return {"status": "down", "error": type(exc).__name__}


class SystemHealthView(APIView):
    """Platform health for Super Admins: services, queues and ingestion."""

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    @extend_schema(responses=inline_serializer("SystemHealth", {"checks": serializers.DictField()}))
    def get(self, request: Request) -> Response:
        from apps.alerts.models import Notification
        from apps.analysis.models import RecomputeRequest
        from apps.reports.models import ReportJob
        from apps.tracking.models import DeviceState

        checks: dict[str, Any] = {}
        started = time.perf_counter()
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            db: dict[str, Any] = {"status": "ok", "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
            if connection.vendor == "postgresql":
                cur.execute("SELECT pg_database_size(current_database()), "
                            "(SELECT count(*) FROM pg_stat_activity WHERE datname = current_database())")
                size, conns = cur.fetchone()
                db.update(size_mb=round(size / 1e6, 1), connections=conns)
        checks["database"] = db
        try:
            import redis
            r = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
            started = time.perf_counter()
            r.ping()
            latency = round((time.perf_counter() - started) * 1000, 1)
            redis_check: dict[str, Any] = {"status": "ok", "latency_ms": latency}
            try:
                redis_check["positions_stream"] = r.xlen("positions")
                pending = r.xpending("positions", "processor")
                redis_check["unacknowledged"] = pending.get("pending", 0) if isinstance(pending, dict) else 0
            except Exception:
                redis_check["positions_stream"] = 0
            checks["redis"] = redis_check
        except Exception as exc:
            checks["redis"] = {"status": "down", "error": type(exc).__name__}
        checks["gateway"] = _probe(settings.GATEWAY_HEALTH_URL)
        checks["processor"] = _probe(settings.PROCESSOR_HEALTH_URL)
        now = timezone.now()
        with rls_bypass():
            states = dict(DeviceState.unscoped.values_list("status").annotate(n=Count("device_id")))
            checks["devices"] = {"by_status": states, "total": sum(states.values())}
            checks["queues"] = {
                "notifications_queued": Notification.unscoped.filter(status="queued").count(),
                "notifications_failed_24h": Notification.unscoped.filter(
                    status="failed", created_at__gte=now - timezone.timedelta(hours=24)).count(),
                "report_jobs_waiting": ReportJob.unscoped.filter(status__in=["queued", "running"]).count(),
                "report_jobs_failed_24h": ReportJob.unscoped.filter(
                    status="failed", created_at__gte=now - timezone.timedelta(hours=24)).count(),
                "recomputes_pending": RecomputeRequest.objects.filter(done_at__isnull=True).count()}
            if connection.vendor == "postgresql":
                with connection.cursor() as cur:
                    cur.execute("SELECT count(*) FILTER (WHERE device_time > now() - interval '5 minutes'), "
                                "count(*) FILTER (WHERE device_time > now() - interval '1 hour') FROM positions "
                                "WHERE device_time > now() - interval '1 hour'")
                    five, hour = cur.fetchone()
                checks["ingestion"] = {"records_5min": five, "records_1h": hour}
        down = [k for k, v in checks.items() if isinstance(v, dict) and v.get("status") == "down"]
        return Response({"status": "degraded" if down else "ok", "down": down, "checked_at": now, "checks": checks})
