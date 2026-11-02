"""Reports and exports (spec A13; Phase 9 done criterion: report and export tests pass)."""
from __future__ import annotations

import csv
import io
from datetime import timedelta
from unittest import mock

import pytest
from django.core import mail
from django.db import connection  # noqa: F401  (used by the skipif condition)
from django.utils import timezone

from apps.alerts.models import Alert
from apps.analysis.models import DrivingBehaviorEvent, IdleEvent, SpeedEvent, Stop, Trip
from apps.audit.models import AuditLog
from apps.device_catalog.models import DeviceModel
from apps.devices.models import Device
from apps.fleet.models import Driver, Vehicle
from apps.geofences.models import Geofence, GeofenceEvent
from apps.reports.models import ReportJob, ScheduledReport
from apps.reports.registry import REPORTS
from apps.reports.tasks import purge_expired_files, run_report_job, run_scheduled_reports

pytestmark = [pytest.mark.django_db, pytest.mark.skipif("connection.vendor != 'postgresql'")]
NOW = timezone.now().replace(microsecond=0)


def seed(org, reg: str, imei: str) -> dict:  # type: ignore[type-arg]
    v = Vehicle.unscoped.create(organization=org, registration_number=reg)
    d = Driver.unscoped.create(organization=org, first_name="Asha" if reg.startswith("T") else "Other", last_name=reg)
    dev = Device.unscoped.create(organization=org, name=f"Tracker {reg}", identifier=imei,
                                 device_model=DeviceModel.objects.get(slug="teltonika-fmc920"))
    base = {"organization": org, "device": dev, "vehicle": v, "driver": d}
    for i in range(3):
        start = NOW - timedelta(days=1, hours=i * 3)
        Trip.unscoped.create(**base, start_time=start, end_time=start + timedelta(minutes=40), start_lat=-6.8,
                             start_lon=39.2, end_lat=-6.7, end_lon=39.3, distance_km=25.5, distance_source="gps",
                             duration_s=2400, driving_s=2100, idle_s=200, stopped_s=100, max_speed_kmh=96,
                             avg_moving_speed_kmh=43.7, points=240, detection_mode="ignition")
    SpeedEvent.unscoped.create(**base, start_time=NOW - timedelta(hours=20), end_time=NOW - timedelta(hours=20) +
                               timedelta(seconds=40), duration_s=40, max_speed_kmh=104, threshold_kmh=80,
                               severity="critical", lat=-6.8, lon=39.2, end_lat=-6.8, end_lon=39.21)
    Stop.unscoped.create(**{k: v for k, v in base.items() if k != "driver"}, start_time=NOW - timedelta(hours=10),
                         end_time=NOW - timedelta(hours=9), duration_s=3600, lat=-6.8, lon=39.2, engine_off=True)
    IdleEvent.unscoped.create(**base, start_time=NOW - timedelta(hours=8),
                              end_time=NOW - timedelta(hours=7, minutes=50), duration_s=600, lat=-6.8, lon=39.2)
    DrivingBehaviorEvent.unscoped.create(**base, type="harsh_braking", occurred_at=NOW - timedelta(hours=5), lat=-6.8,
                                         lon=39.2, speed_kmh=50)
    fence = Geofence.unscoped.create(organization=org, name=f"Depot {reg}", kind="circle", center_lat=-6.8,
                                     center_lon=39.2, radius_m=200)
    GeofenceEvent.unscoped.create(organization=org, geofence=fence, device=dev, vehicle=v, event="enter",
                                  occurred_at=NOW - timedelta(hours=4), lat=-6.8, lon=39.2)
    for kind, st in (("overspeed", "new"), ("device_offline", "resolved")):
        Alert.unscoped.create(organization=org, vehicle=v, device=dev, type=kind, severity="warning", status=st,
                              message=f"{reg} {kind}", occurred_at=NOW - timedelta(hours=3),
                              last_occurred_at=NOW - timedelta(hours=3),
                              resolved_at=NOW - timedelta(hours=2) if st == "resolved" else None)
    from apps.maintenance.models import MaintenanceSchedule, ServiceRecord
    sched = MaintenanceSchedule.unscoped.create(organization=org, vehicle=v, name=f"Oil {reg}", interval_km=50,
                                                last_service_at=NOW - timedelta(days=2))
    ServiceRecord.unscoped.create(organization=org, vehicle=v, schedule=sched, title=f"Tyres {reg}",
                                  performed_at=NOW - timedelta(hours=30), cost=120000)
    return {"vehicle": v, "driver": d, "device": dev}


@pytest.fixture
def data(org_a, org_b):  # type: ignore[no-untyped-def]
    return seed(org_a, "T 123 ABC", "356307042441013"), seed(org_b, "X 999 SEC", "490154203237518")


def q(**extra: str) -> str:
    start = (NOW - timedelta(days=3)).isoformat().replace("+00:00", "Z")
    end = (NOW + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    return "&".join([f"start={start}", f"end={end}", *[f"{k}={v}" for k, v in extra.items()]])


def test_every_report_runs_and_never_shows_another_organization(org_a, make_member, api, data):
    client = api(make_member(org_a, "fleet_manager"))
    catalog = client.get("/api/v1/reports/").json()["reports"]
    assert {r["key"] for r in catalog} == set(REPORTS) and len(catalog) == 14
    for key in REPORTS:
        body = client.get(f"/api/v1/reports/{key}/?{q()}").json()
        assert body["count"] >= 1, key
        flat = str(body["rows"])
        assert "X 999 SEC" not in flat and "490154203237518" not in flat and "Other" not in flat, key


def test_values_filters_and_score(org_a, make_member, api, data):
    client = api(make_member(org_a, "fleet_manager"))
    trips = client.get(f"/api/v1/reports/trips/?{q()}").json()
    assert trips["count"] == 3 and trips["rows"][0]["duration"] == "0:40:00" and trips["rows"][0]["distance"] == 25.5
    summary = client.get(f"/api/v1/reports/fleet_summary/?{q()}").json()["rows"]
    mine = next(r for r in summary if r["vehicle"] == "T 123 ABC")
    assert mine["trips"] == 3 and mine["distance"] == 76.5 and mine["overspeed"] == 1 and mine["alerts"] == 2
    driver = client.get(f"/api/v1/reports/driver/?{q()}").json()["rows"][0]
    # 1 overspeed (5) + 1 harsh braking (3) over 76.5 km (counted per 100 km, minimum 1): 100 - 8 = 92
    assert (driver["overspeed"], driver["harsh"], driver["score"]) == (1, 1, 92)
    assert client.get(f"/api/v1/reports/alerts/?{q(status='open')}").json()["count"] == 1
    other = Vehicle.unscoped.create(organization=org_a, registration_number="T 2 EMPTY")
    assert client.get(f"/api/v1/reports/trips/?{q(vehicle=str(other.pk))}").json()["count"] == 0
    assert client.get("/api/v1/reports/trips/?start=nope").status_code == 400
    assert client.get("/api/v1/reports/nonsense/").status_code == 404
    health = client.get("/api/v1/reports/device_health/").json()["rows"]
    assert health[0]["status"] == "NEVER_CONNECTED"


def test_exports_csv_excel_pdf_are_valid_and_audited(org_a, make_member, api, data):
    from openpyxl import load_workbook
    client = api(make_member(org_a, "fleet_manager"))
    url = "/api/v1/reports/trips/export/"
    filters = {"start": (NOW - timedelta(days=3)).isoformat(), "end": (NOW + timedelta(hours=1)).isoformat()}
    resp = client.post(url, {"format": "csv", "filters": filters}, format="json")
    assert resp.status_code == 200 and resp["Content-Disposition"].startswith('attachment; filename="trips_')
    rows = list(csv.reader(io.StringIO(resp.content.decode("utf-8-sig"))))
    assert rows[0][:3] == ["Vehicle", "Driver", "Start"] and len(rows) == 4 and rows[1][5] == "25.5"
    xlsx = client.post(url, {"format": "xlsx", "filters": filters}, format="json")
    sheet = load_workbook(io.BytesIO(xlsx.content)).active
    values = list(sheet.iter_rows(values_only=True))
    assert values[0][0] == "Trips" and values[3][0] == "Vehicle" and values[4][5] == 25.5   # numbers stay numbers
    pdf = client.post(url, {"format": "pdf", "filters": filters}, format="json")
    assert pdf["Content-Type"] == "application/pdf" and pdf.content.startswith(b"%PDF-") and len(pdf.content) > 1500
    entries = AuditLog.objects.filter(action="reports.exported").order_by("id")
    assert [e.metadata["format"] for e in entries] == ["csv", "xlsx", "pdf"] and entries[0].metadata["rows"] == 3


def test_large_exports_run_as_jobs_with_expiring_links(org_a, make_member, api, data, settings, django_capture_on_commit_callbacks):  # noqa: E501
    settings.REPORT_SYNC_MAX_ROWS = 1                                  # force the background path
    user = make_member(org_a, "fleet_manager")
    client = api(user)
    with mock.patch("apps.reports.views.run_report_job.delay") as delay, \
            django_capture_on_commit_callbacks(execute=True):
        queued = client.post("/api/v1/reports/trips/export/", {"format": "xlsx", "filters": {}}, format="json")
    assert queued.status_code == 202 and queued.json()["status"] == "queued"
    job_id = queued.json()["id"]
    delay.assert_called_once_with(job_id)
    assert run_report_job(job_id) == "done"
    job = client.get(f"/api/v1/report-jobs/{job_id}/").json()
    assert job["status"] == "done" and job["progress"] == 100 and job["row_count"] == 3 and job["downloadable"]
    got = client.get(f"/api/v1/report-jobs/{job_id}/download/")
    assert got.status_code == 200 and b"".join(got.streaming_content)[:2] == b"PK"           # xlsx is a zip
    stranger = api(make_member(org_a, "fleet_manager", "other@a.test"))
    assert stranger.get(f"/api/v1/report-jobs/{job_id}/download/").status_code == 404     # someone else's export
    ReportJob.unscoped.filter(pk=job_id).update(expires_at=timezone.now() - timedelta(minutes=1))
    assert client.get(f"/api/v1/report-jobs/{job_id}/download/").status_code == 410
    assert purge_expired_files() == 1 and not ReportJob.unscoped.get(pk=job_id).file
    settings.REPORT_SYNC_MAX_ROWS = 10_000
    long = {"start": (NOW - timedelta(days=60)).isoformat(), "end": NOW.isoformat()}
    with mock.patch("apps.reports.views.run_report_job.delay"):
        assert client.post("/api/v1/reports/trips/export/", {"format": "csv", "filters": long},
                           format="json").status_code == 202                             # more than 31 days


def test_scheduled_reports_are_emailed(org_a, make_member, api, data):
    client = api(make_member(org_a, "fleet_manager"))
    made = client.post("/api/v1/scheduled-reports/", {"name": "Daily trips", "report": "trips", "frequency": "daily",
                                                      "format": "csv", "recipients": ["boss@demo.test"]}, format="json")
    assert made.status_code == 201 and made.json()["next_run_at"] > timezone.now().isoformat()
    assert client.post("/api/v1/scheduled-reports/", {"name": "X", "report": "nope", "frequency": "daily",
                                                      "recipients": ["a@b.test"]}, format="json").status_code == 400
    assert client.post("/api/v1/scheduled-reports/", {"name": "X", "report": "trips", "frequency": "daily",
                                                      "recipients": ["a@b.test"], "filters": {"start": "x"}},
                       format="json").status_code == 400
    ScheduledReport.unscoped.filter(pk=made.json()["id"]).update(next_run_at=timezone.now() - timedelta(minutes=1))
    assert run_scheduled_reports() == 1
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["boss@demo.test"]
    name, content, ctype = mail.outbox[0].attachments[0]
    assert name.startswith("trips_") and ctype == "text/csv"
    sched = ScheduledReport.unscoped.get(pk=made.json()["id"])
    assert sched.next_run_at > timezone.now() and sched.last_run_at and not sched.last_error
    assert AuditLog.objects.filter(action="reports.scheduled_sent").exists()
    assert AuditLog.objects.filter(action="scheduled_reports.created").exists()


@pytest.mark.parametrize("role,view,export", [("viewer", True, False), ("driver", False, False),
                                              ("fleet_manager", True, True)])
def test_permissions(org_a, make_member, api, data, role, view, export):
    client = api(make_member(org_a, role))
    assert (client.get(f"/api/v1/reports/trips/?{q()}").status_code == 200) is view
    resp = client.post("/api/v1/reports/trips/export/", {"format": "csv", "filters": {}}, format="json")
    assert (resp.status_code == 200) is export


def test_maintenance_report_shows_services_and_due_status(org_a, make_member, api, data):
    client = api(make_member(org_a, "fleet_manager"))
    rows = client.get(f"/api/v1/reports/maintenance/?{q()}").json()["rows"]
    service = next(r for r in rows if r["kind"] == "service")
    schedule = next(r for r in rows if r["kind"] == "schedule")
    assert service["item"] == "Tyres T 123 ABC" and service["cost"] == 120000
    # 3 trips of 25.5 km since the last service against a 50 km interval: overdue by 26.5 km
    assert schedule["status"] == "overdue" and schedule["km_left"] == -26.5
    assert client.get(f"/api/v1/reports/maintenance/?{q(status='ok')}").json()["count"] == 1   # only the service
