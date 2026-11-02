"""Reports API: catalogue, on-screen report, exports (sync or background job), jobs and schedules (spec A13)."""
from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import transaction
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import mixins, serializers, status
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from apps.audit.services import record
from apps.reports.models import FORMATS, ReportJob, ScheduledReport
from apps.reports.registry import REPORTS
from apps.reports.service import filename, filters_json, get_report, heading, parse_filters
from apps.reports.tasks import next_run, run_report_job
from apps.reports.writers import CONTENT_TYPES, fmt, render
from common.crud import TenantCrudViewSet
from common.exceptions import ApiError
from common.middleware import client_ip
from common.viewsets import TenantViewSet


def _columns(report: Any) -> list[dict[str, str]]:
    return [{"key": c.key, "label": c.label, "kind": c.kind} for c in report.columns]


class ExportSerializer(serializers.Serializer):  # type: ignore[type-arg]
    format = serializers.ChoiceField(choices=FORMATS)
    filters = serializers.DictField(required=False, default=dict)


REPORT_KEY = OpenApiParameter("id", OpenApiTypes.STR, OpenApiParameter.PATH, description="Report key, e.g. trips")


class ReportViewSet(TenantViewSet):
    lookup_value_regex = "[a-z0-9_]+"
    required_permissions = {"list": "reports.view", "retrieve": "reports.view", "export": "reports.export"}

    @extend_schema(responses=inline_serializer("ReportCatalog", {"reports": serializers.ListField()}))
    def list(self, request: Request) -> Response:
        return Response({"reports": [{"key": r.key, "title": r.title, "columns": _columns(r),
                                      "filters": list(r.filters), "chart": r.chart} for r in REPORTS.values()]})

    @extend_schema(parameters=[REPORT_KEY], responses=inline_serializer("ReportPage", {"rows": serializers.ListField(),
                                                              "count": serializers.IntegerField()}))
    def retrieve(self, request: Request, pk: str | None = None) -> Response:
        report = get_report(str(pk))
        f = parse_filters(request.query_params)
        rows = report.rows(f)
        page_size = min(max(int(request.query_params.get("page_size", 50) or 50), 1), 500)
        page = max(int(request.query_params.get("page", 1) or 1), 1)
        chunk = rows[(page - 1) * page_size: page * page_size]
        chart = None
        if report.chart:
            label, value = report.chart
            chart = [{"label": str(r.get(label) or ""), "value": float(r.get(value) or 0)} for r in rows[:50]]
        return Response({"key": report.key, "title": report.title, "columns": _columns(report), "count": len(rows),
                         "page": page, "page_size": page_size, "chart": chart,
                         "rows": [{c.key: fmt(c, r.get(c.key)) for c in report.columns} for r in chunk]})

    @extend_schema(parameters=[REPORT_KEY], request=ExportSerializer,
                   responses={(200, "application/octet-stream"): OpenApiTypes.BINARY,
                              202: inline_serializer("ReportJobQueued", {"id": serializers.IntegerField()})})
    @action(detail=True, methods=["post"])
    def export(self, request: Request, pk: str | None = None) -> HttpResponse | Response:
        """Small exports are returned at once; above 10,000 rows or 31 days a background job is queued."""
        report = get_report(str(pk))
        body = ExportSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        fmt_ = body.validated_data["format"]
        f = parse_filters(body.validated_data["filters"])
        org = self.tenant.organization
        meta = {"report": report.key, "format": fmt_, **{k: v for k, v in filters_json(f).items() if v}}
        long_period = (f.end - f.start).days > settings.REPORT_SYNC_MAX_DAYS
        rows = None if long_period else report.rows(f)
        if rows is not None and len(rows) <= settings.REPORT_SYNC_MAX_ROWS:
            data = render(report, rows, fmt_, heading(org.name, f))
            record("reports.exported", user=request.user, organization=org, ip=client_ip(request),
                   metadata={**meta, "rows": len(rows)})
            response = HttpResponse(data, content_type=CONTENT_TYPES[fmt_])
            response["Content-Disposition"] = f'attachment; filename="{filename(report, f, fmt_)}"'
            return response
        with transaction.atomic():
            job = ReportJob.objects.create(organization=org, user=request.user, report=report.key,
                                           filters=filters_json(f), format=fmt_)
            record("reports.exported", user=request.user, organization=org, resource=job, ip=client_ip(request),
                   metadata={**meta, "background": True})
            transaction.on_commit(lambda: run_report_job.delay(job.pk))
        return Response(JobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class JobSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    downloadable = serializers.SerializerMethodField()

    class Meta:
        model = ReportJob
        fields = ["id", "report", "format", "status", "progress", "row_count", "filename", "error", "created_at",
                  "finished_at", "expires_at", "downloadable"]

    def get_downloadable(self, job: ReportJob) -> bool:
        return job.status == "done" and bool(job.file) and bool(job.expires_at and job.expires_at > timezone.now())


class ReportJobViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, TenantViewSet):
    """The signed-in user's background exports, with progress and a download link valid for 24 hours."""

    serializer_class = JobSerializer
    required_permissions = {"list": "reports.export", "retrieve": "reports.export", "download": "reports.export"}

    def get_queryset(self) -> Any:
        if getattr(self, "swagger_fake_view", False):
            return ReportJob.unscoped.none()
        return ReportJob.objects.filter(user=self.request.user)

    @extend_schema(responses={(200, "application/octet-stream"): OpenApiTypes.BINARY})
    @action(detail=True, methods=["get"])
    def download(self, request: Request, pk: str | None = None) -> FileResponse:
        job = self.get_object()
        if not JobSerializer().get_downloadable(job):
            raise ApiError("not_available", "This export is not ready or its link has expired.", 410
                           if job.status == "done" else 409)
        return FileResponse(job.file.open("rb"), as_attachment=True, filename=job.filename,
                            content_type=CONTENT_TYPES[job.format])


class ScheduleSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    recipients = serializers.ListField(child=serializers.EmailField(), min_length=1, max_length=20)

    class Meta:
        model = ScheduledReport
        fields = ["id", "name", "report", "filters", "frequency", "format", "recipients", "is_active", "next_run_at",
                  "last_run_at", "last_error", "created_at"]
        read_only_fields = ["id", "next_run_at", "last_run_at", "last_error", "created_at"]

    def validate_report(self, value: str) -> str:
        if value not in REPORTS:
            raise serializers.ValidationError("Unknown report.")
        return value

    def validate_filters(self, value: dict[str, Any]) -> dict[str, Any]:
        allowed = {"vehicle", "group", "driver", "status"}
        if set(value) - allowed:
            raise serializers.ValidationError("Only vehicle, group, driver and status can be fixed; the period "
                                              "follows the frequency.")
        return value

    def create(self, validated: dict[str, Any]) -> ScheduledReport:
        validated["next_run_at"] = next_run(validated["frequency"], timezone.now())
        request = self.context.get("request")
        if request is not None:
            validated["created_by"] = request.user
        return super().create(validated)  # type: ignore[no-any-return]

    def update(self, instance: ScheduledReport, validated: dict[str, Any]) -> ScheduledReport:
        if "frequency" in validated and validated["frequency"] != instance.frequency:
            validated["next_run_at"] = next_run(validated["frequency"], timezone.now())
        return super().update(instance, validated)  # type: ignore[no-any-return]


class ScheduledReportViewSet(TenantCrudViewSet):
    model = ScheduledReport
    audit_resource = "scheduled_reports"
    soft_delete = False
    serializer_class = ScheduleSerializer
    search_fields = ("name",)
    required_permissions = {"list": "reports.view", "retrieve": "reports.view", "create": "reports.export",
                            "update": "reports.export", "partial_update": "reports.export",
                            "destroy": "reports.export"}
