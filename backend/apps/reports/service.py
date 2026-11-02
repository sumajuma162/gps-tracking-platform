"""Running reports inside an organization's scope (request, Celery job or schedule)."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.reports.registry import REPORTS, Filters, Report
from common.exceptions import ApiError
from common.rls import is_postgres, set_current_org
from common.tenancy import tenant_context

MAX_DAYS = 366


@contextmanager
def org_scope(org_id: int) -> Iterator[None]:
    """Application scope and database RLS for one organization (outside a request)."""
    with tenant_context(org_id):
        if is_postgres():
            set_current_org(org_id)
        try:
            yield
        finally:
            if is_postgres():
                set_current_org(None)


def parse_filters(data: dict[str, Any]) -> Filters:
    def when(key: str) -> datetime | None:
        raw = data.get(key)
        if not raw:
            return None
        value = parse_datetime(str(raw))
        if value is None:
            raise ApiError("invalid_filter", f"{key} must be an ISO date-time.")
        return value if timezone.is_aware(value) else timezone.make_aware(value)

    end = when("end") or timezone.now()
    start = when("start") or end - timedelta(days=7)
    if end <= start:
        raise ApiError("invalid_filter", "The end must be after the start.")
    if (end - start).days > MAX_DAYS:
        raise ApiError("invalid_filter", f"Choose at most {MAX_DAYS} days.")

    def ident(key: str) -> int | None:
        raw = data.get(key)
        if raw in (None, ""):
            return None
        if not str(raw).isdigit():
            raise ApiError("invalid_filter", f"{key} must be an id.")
        return int(raw)

    status = data.get("status") or None
    return Filters(start=start, end=end, vehicle=ident("vehicle"), group=ident("group"), driver=ident("driver"),
                   status=str(status) if status else None)


def get_report(key: str) -> Report:
    report = REPORTS.get(key)
    if report is None:
        raise ApiError("not_found", "Unknown report.", 404)
    return report


def heading(org_name: str, f: Filters) -> str:
    fmt = "%Y-%m-%d %H:%M"
    return (f"{org_name} · {timezone.localtime(f.start).strftime(fmt)} – {timezone.localtime(f.end).strftime(fmt)}"
            f" · generated {timezone.localtime().strftime(fmt)}")


def filename(report: Report, f: Filters, ext: str) -> str:
    return f"{report.key}_{timezone.localtime(f.start):%Y%m%d}-{timezone.localtime(f.end):%Y%m%d}.{ext}"


def filters_json(f: Filters) -> dict[str, Any]:
    return {"start": f.start.isoformat(), "end": f.end.isoformat(), "vehicle": f.vehicle, "group": f.group,
            "driver": f.driver, "status": f.status}
