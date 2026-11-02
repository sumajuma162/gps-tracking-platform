from __future__ import annotations

from django.db.models import QuerySet
from django.utils.dateparse import parse_datetime
from rest_framework import mixins, serializers
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from apps.audit.models import AuditLog
from common.viewsets import PlatformViewSet, TenantViewSet


class AuditLogSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    user_email = serializers.EmailField(source="user.email", default=None, read_only=True)
    organization_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = AuditLog
        fields = ["id", "created_at", "action", "user_email", "organization_id", "resource_type", "resource_id",
                  "ip_address", "user_agent", "metadata"]


def _filter(qs: QuerySet[AuditLog], params: dict[str, str]) -> QuerySet[AuditLog]:
    """Search (spec A13): action prefix, user, resource type and id, IP, time range, and free text ``q`` over the
    action, resource and metadata (secrets are already redacted when entries are written)."""
    from django.db.models import Q, TextField
    from django.db.models.functions import Cast

    if params.get("action"):
        qs = qs.filter(action__startswith=params["action"])
    if params.get("user"):
        qs = qs.filter(user__email__icontains=params["user"])
    if params.get("resource_type"):
        qs = qs.filter(resource_type=params["resource_type"])
    if params.get("resource_id"):
        qs = qs.filter(resource_id=params["resource_id"])
    if params.get("ip"):
        qs = qs.filter(ip_address=params["ip"])
    if term := params.get("q", "").strip()[:100]:
        qs = qs.annotate(meta_text=Cast("metadata", TextField())).filter(
            Q(action__icontains=term) | Q(resource_type__icontains=term) | Q(resource_id=term)
            | Q(user__email__icontains=term) | Q(meta_text__icontains=term))
    for key, lookup in (("since", "created_at__gte"), ("until", "created_at__lt")):
        if params.get(key) and (value := parse_datetime(params[key])):
            qs = qs.filter(**{lookup: value})
    return qs


class AuditLogViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, TenantViewSet):
    serializer_class = AuditLogSerializer
    required_permissions = {"list": "audit.view", "retrieve": "audit.view", "facets": "audit.view"}

    @action(detail=False, methods=["get"])
    def facets(self, request: Request) -> Response:
        """Distinct action and resource names for the search filters."""
        qs = AuditLog.objects.filter(organization=self.tenant.organization)
        return Response({"actions": sorted(set(qs.values_list("action", flat=True).distinct()[:500])),
                         "resource_types": sorted(set(qs.exclude(resource_type="")
                                                      .values_list("resource_type", flat=True).distinct()[:200]))})

    def get_queryset(self) -> QuerySet[AuditLog]:
        if getattr(self, "swagger_fake_view", False):
            return AuditLog.objects.none()
        qs = AuditLog.objects.filter(organization=self.tenant.organization).select_related("user")
        return _filter(qs, self.request.query_params)


class PlatformAuditLogViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, PlatformViewSet):
    serializer_class = AuditLogSerializer

    def get_queryset(self) -> QuerySet[AuditLog]:
        qs = AuditLog.objects.select_related("user")
        if org := self.request.query_params.get("organization"):
            qs = qs.filter(organization_id=org)
        return _filter(qs, self.request.query_params)
