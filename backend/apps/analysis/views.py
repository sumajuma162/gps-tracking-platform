"""Read-only APIs for the processor's analysis results (spec A13 endpoint groups: trips, stops)."""
from __future__ import annotations

from typing import Any, ClassVar

from django.db.models import Model, QuerySet
from django.utils.dateparse import parse_datetime
from rest_framework import mixins, serializers

from apps.analysis.models import DrivingBehaviorEvent, IdleEvent, SpeedEvent, Stop, Trip
from apps.geofences.models import GeofenceEvent
from common.exceptions import ApiError
from common.viewsets import TenantViewSet


def serializer_for(model: type[Model], *extra: str) -> type[serializers.ModelSerializer]:  # type: ignore[type-arg]
    meta = type("Meta", (), {"model": model, "exclude": ["organization"]})
    attrs: dict[str, Any] = {"Meta": meta,
                             "vehicle_registration": serializers.CharField(source="vehicle.registration_number",
                                                                           default=None, read_only=True),
                             "driver_name": serializers.CharField(source="driver.full_name", default=None,
                                                                  read_only=True)}
    if "geofence_name" in extra:
        attrs["geofence_name"] = serializers.CharField(source="geofence.name", read_only=True)
    return type(f"{model.__name__}Serializer", (serializers.ModelSerializer,), attrs)


class EventListViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, TenantViewSet):
    """Filters: ``vehicle``, ``device``, ``start`` / ``end`` (ISO times; overlap with the event)."""

    model: ClassVar[type[Model]]
    time_fields: ClassVar[tuple[str, str]] = ("start_time", "end_time")

    def get_queryset(self) -> QuerySet[Any]:
        if getattr(self, "swagger_fake_view", False):
            return self.model.unscoped.none()  # type: ignore[attr-defined, no-any-return]
        qs = self.model.objects.select_related("vehicle", "driver")  # type: ignore[attr-defined]
        p = self.request.query_params
        for key in ("vehicle", "device", "driver"):
            if p.get(key):
                if not p[key].isdigit():
                    raise ApiError("invalid_filter", f"{key} must be an id.")
                qs = qs.filter(**{f"{key}_id": int(p[key])})
        first, last = self.time_fields
        for key, lookup in (("start", f"{last}__gte"), ("end", f"{first}__lt")):
            if p.get(key):
                when = parse_datetime(p[key])
                if when is None:
                    raise ApiError("invalid_filter", f"{key} must be an ISO date-time.")
                qs = qs.filter(**{lookup: when})
        return qs  # type: ignore[no-any-return]


def _viewset(model: type[Model], times: tuple[str, str] = ("start_time", "end_time"),
             permission: str = "trips.view", *extra: str) -> type[EventListViewSet]:
    return type(f"{model.__name__}ViewSet", (EventListViewSet,), {
        "model": model, "time_fields": times, "serializer_class": serializer_for(model, *extra),
        "required_permissions": {"list": permission, "retrieve": permission}})


TripViewSet = _viewset(Trip)
StopViewSet = _viewset(Stop)
IdleEventViewSet = _viewset(IdleEvent)
SpeedEventViewSet = _viewset(SpeedEvent)
DrivingEventViewSet = _viewset(DrivingBehaviorEvent, ("occurred_at", "occurred_at"))
GeofenceEventViewSet = _viewset(GeofenceEvent, ("occurred_at", "occurred_at"), "geofences.view", "geofence_name")
