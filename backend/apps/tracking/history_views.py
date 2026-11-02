"""Route history API and exports (spec A12)."""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from apps.audit.services import record
from apps.fleet.models import Vehicle
from apps.tracking import history
from common.exceptions import ApiError
from common.middleware import client_ip
from common.viewsets import TenantViewSet


class HistoryQuery(serializers.Serializer):  # type: ignore[type-arg]
    vehicle = serializers.IntegerField()
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()
    zoom = serializers.IntegerField(min_value=1, max_value=22, default=13)
    stop_minutes = serializers.FloatField(min_value=1, max_value=240, default=3)
    bbox = serializers.CharField(required=False, help_text="south,west,north,east: detail for the visible area only")

    def validate_bbox(self, value: str) -> tuple[float, float, float, float]:
        try:
            south, west, north, east = (float(v) for v in value.split(","))
        except ValueError as exc:
            raise serializers.ValidationError("Use south,west,north,east.") from exc
        if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
            raise serializers.ValidationError("Not a valid area.")
        return south, west, north, east

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs["end"] <= attrs["start"]:
            raise serializers.ValidationError({"end": "The end must be after the start."})
        if attrs["end"] - attrs["start"] > history.max_range():
            days = history.max_range().days
            raise serializers.ValidationError({"end": f"Choose at most {days} days at a time."})
        return attrs


class ExportQuery(HistoryQuery):
    # Not "format": DRF reserves ?format= for picking a response renderer and answers 404 for unknown ones.
    type = serializers.ChoiceField(choices=["csv", "gpx", "kml"])


QUERY_PARAMS = [OpenApiParameter(n, t, required=r) for n, t, r in (
    ("vehicle", OpenApiTypes.INT, True), ("start", OpenApiTypes.DATETIME, True),
    ("end", OpenApiTypes.DATETIME, True), ("zoom", OpenApiTypes.INT, False),
    ("stop_minutes", OpenApiTypes.NUMBER, False))]


def _filename(vehicle: Vehicle, start: datetime, ext: str) -> str:
    reg = "".join(c if c.isalnum() else "-" for c in vehicle.registration_number).strip("-")
    return f"{reg}_{timezone.localtime(start):%Y-%m-%d}.{ext}"


def _csv(pts: list[history.Point]) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["time_utc", "latitude", "longitude", "speed_kmh", "heading", "ignition"])
    for p in pts:
        w.writerow([p.t.isoformat(), f"{p.lat:.6f}", f"{p.lon:.6f}", f"{p.speed:.1f}",
                    "" if p.heading is None else round(p.heading), "" if p.ignition is None else int(p.ignition)])
    return out.getvalue()


def _gpx(pts: list[history.Point], name: str) -> str:
    seg = "".join(f'<trkpt lat="{p.lat:.6f}" lon="{p.lon:.6f}"><time>{p.t.isoformat()}</time>'
                  f"<speed>{p.speed / 3.6:.2f}</speed></trkpt>" for p in pts)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="Fleet tracking" '
            'xmlns="http://www.topografix.com/GPX/1/1">'
            f"<trk><name>{escape(name)}</name><trkseg>{seg}</trkseg></trk></gpx>\n")


def _kml(pts: list[history.Point], stops: list[dict[str, Any]], name: str) -> str:
    coords = " ".join(f"{p.lon:.6f},{p.lat:.6f}" for p in pts)
    marks = "".join(f"<Placemark><name>{escape('Stop ' + str(i + 1))}</name>"
                    f"<description>{escape(s['start'] + ' - ' + s['end'])}</description>"
                    f"<Point><coordinates>{s['longitude']:.6f},{s['latitude']:.6f}</coordinates></Point></Placemark>"
                    for i, s in enumerate(stops))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            f"<name>{escape(name)}</name><Placemark><name>{escape(name)}</name><LineString><tessellate>1</tessellate>"
            f"<coordinates>{coords}</coordinates></LineString></Placemark>{marks}</Document></kml>\n")


class HistoryViewSet(TenantViewSet):
    tenant_policy = "history"
    required_permissions = {"list": "routes.view_history", "export": ("routes.view_history", "reports.export")}

    def _query(self, request: Request, serializer_class: type[HistoryQuery]) -> tuple[Vehicle, dict[str, Any]]:
        q = serializer_class(data=request.query_params)
        q.is_valid(raise_exception=True)
        vehicle = Vehicle.objects.filter(pk=q.validated_data["vehicle"], deleted_at__isnull=True).first()
        if vehicle is None:   # other organizations' vehicles look exactly like missing ones
            raise ApiError("not_found", "Vehicle not found.", 404)
        return vehicle, q.validated_data

    @extend_schema(parameters=QUERY_PARAMS, responses=inline_serializer("RouteHistory", {
        "points": serializers.ListField(child=serializers.ListField()),
        "stops": serializers.ListField(child=serializers.DictField()), "summary": serializers.DictField()}))
    def list(self, request: Request) -> Response:
        """Simplified route (``[epoch_s, lat, lon, speed_kmh, heading, ignition]``), stops and summary."""
        vehicle, d = self._query(request, HistoryQuery)
        if d.get("bbox"):
            return Response(history.detail(vehicle.pk, d["start"], d["end"], d["zoom"], d["bbox"]))
        return Response(history.build(vehicle.pk, d["start"], d["end"], d["zoom"], d["stop_minutes"]))

    @extend_schema(parameters=[*QUERY_PARAMS, OpenApiParameter("type", OpenApiTypes.STR, required=True,
                                                               enum=["csv", "gpx", "kml"])],
                   responses={(200, "application/octet-stream"): OpenApiTypes.BINARY})
    @action(detail=False, methods=["get"])
    def export(self, request: Request) -> HttpResponse:
        """Full-resolution route as CSV, GPX or KML (audited)."""
        vehicle, d = self._query(request, ExportQuery)
        pts = history.fetch(vehicle.pk, d["start"], d["end"])
        if not pts:
            raise ApiError("no_data", "There is no route in this period.", 404)
        fmt = d["type"]
        name = f"{vehicle.registration_number} {timezone.localtime(d['start']):%Y-%m-%d}"
        if fmt == "csv":
            body, ctype = _csv(pts), "text/csv; charset=utf-8"
        elif fmt == "gpx":
            body, ctype = _gpx(pts, name), "application/gpx+xml"
        else:
            stops = [s.as_dict(pts) for s in history.detect_stops(pts, speed_kmh=settings.STOP_SPEED_KMH,
                                                                   min_seconds=int(d["stop_minutes"] * 60))]
            body, ctype = _kml(pts, stops, name), "application/vnd.google-earth.kml+xml"
        record("routes.exported", user=request.user, organization=self.tenant.organization, resource=vehicle,
               ip=client_ip(request), metadata={"format": fmt, "points": len(pts), "start": d["start"].isoformat(),
                                                "end": d["end"].isoformat()})
        response = HttpResponse(body, content_type=ctype)
        response["Content-Disposition"] = f"attachment; filename={quoteattr(_filename(vehicle, d['start'], fmt))}"
        return response
