"""Automatic tenant-isolation and authentication coverage (spec A8, A16).

Every route in the API router must have an isolation factory below. Adding a new
endpoint without one makes ``test_every_route_is_covered`` fail.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from apps.audit.models import AuditLog
from apps.organizations.models import Organization, OrganizationMembership
from apps.rbac.models import Role
from config.api_urls import router

pytestmark = pytest.mark.django_db


def _membership(org: Organization) -> int:
    from apps.accounts.models import User
    user = User.objects.create_user(f"victim@{org.slug}.test", "x")
    return OrganizationMembership.unscoped.create(
        organization=org, user=user, role=Role.objects.get(slug="viewer", organization__isnull=True)).pk


def _role(org: Organization) -> int:
    return Role.objects.create(organization=org, slug="secret-role", name="Secret role").pk


def _audit(org: Organization) -> int:
    return AuditLog.objects.create(organization=org, action="secret.event").pk


def _sim(org: Organization) -> int:
    from apps.fleet.models import SimCard
    return SimCard.unscoped.create(organization=org, phone_number="+255700000001").pk


def _group(org: Organization) -> int:
    from apps.fleet.models import VehicleGroup
    return VehicleGroup.unscoped.create(organization=org, name="Secret group").pk


def _vehicle(org: Organization) -> int:
    from apps.fleet.models import Vehicle
    return Vehicle.unscoped.create(organization=org, registration_number="T 999 SEC").pk


def _driver(org: Organization) -> int:
    from apps.fleet.models import Driver
    return Driver.unscoped.create(organization=org, first_name="Secret", last_name="Driver").pk


def _device(org: Organization) -> int:
    from apps.device_catalog.models import DeviceModel
    from apps.devices.models import Device
    return Device.unscoped.create(organization=org, name="Secret tracker", identifier="490154203237518",
                                  device_model=DeviceModel.objects.get(slug="teltonika-fmc920")).pk


def _unregistered() -> int:
    from django.utils import timezone

    from apps.devices.models import UnregisteredDeviceAttempt
    now = timezone.now()
    return UnregisteredDeviceAttempt.objects.create(identifier="352093086403655", protocol="teltonika",
                                                    transport="tcp", port=5027, last_peer="203.0.113.9:4000",
                                                    first_seen_at=now, last_seen_at=now).pk


def _analysis(model_path: str, **extra):  # type: ignore[no-untyped-def]
    def make(org: Organization) -> int:
        from django.apps import apps
        from django.utils import timezone
        now = timezone.now()
        device = Device_for(org)
        fields = {"organization": org, "device": device, **extra}
        model = apps.get_model(model_path)
        names = {f.name for f in model._meta.fields}
        for name, value in (("start_time", now), ("end_time", now), ("occurred_at", now), ("duration_s", 60),
                            ("lat", -6.8), ("lon", 39.2), ("end_lat", -6.8), ("end_lon", 39.2), ("distance_km", 1.0),
                            ("distance_source", "gps"), ("driving_s", 1), ("idle_s", 0), ("stopped_s", 0),
                            ("max_speed_kmh", 90.0), ("avg_moving_speed_kmh", 40.0), ("points", 3),
                            ("detection_mode", "ignition"), ("engine_off", True), ("threshold_kmh", 80.0),
                            ("severity", "warning"), ("type", "crash"), ("event", "enter")):
            if name in names and name not in fields:
                fields[name] = value
        if "geofence" in names:
            fields["geofence_id"] = _geofence(org)
        return model.unscoped.create(**fields).pk
    return make


def Device_for(org: Organization):  # type: ignore[no-untyped-def]  # noqa: N802
    from apps.device_catalog.models import DeviceModel
    from apps.devices.models import Device
    return Device.unscoped.get_or_create(organization=org, identifier="356307042441013", defaults={
        "name": "Iso", "device_model": DeviceModel.objects.get(slug="teltonika-fmc920")})[0]


def _geofence(org: Organization) -> int:
    from apps.geofences.models import Geofence
    return Geofence.unscoped.get_or_create(organization=org, name="Secret depot", defaults={
        "kind": "circle", "center_lat": -6.8, "center_lon": 39.2, "radius_m": 200})[0].pk


def _rule(org: Organization) -> int:
    from apps.geofences.models import GeofenceRule
    return GeofenceRule.unscoped.create(organization=org, geofence_id=_geofence(org), name="Secret rule").pk


def _alert_rule(org: Organization) -> int:
    from apps.alerts.models import AlertRule
    return AlertRule.unscoped.create(organization=org, name="Secret rule", type="overspeed",
                                     recipient_roles=["org_admin"]).pk


def _alert(org: Organization) -> int:
    from django.utils import timezone

    from apps.alerts.models import Alert
    now = timezone.now()
    return Alert.unscoped.create(organization=org, type="crash", severity="critical", message="secret",
                                 occurred_at=now, last_occurred_at=now).pk


def _notification(org: Organization) -> int:
    from apps.accounts.models import User
    from apps.alerts.models import Notification
    user = User.objects.filter(email=f"victim{org.pk}@iso.test").first() or User.objects.create_user(
        email=f"victim{org.pk}@iso.test", password="Victim-Pass-2026!")
    return Notification.unscoped.create(organization=org, user=user, channel="in_app", title="secret").pk


def _preference(org: Organization) -> int:
    from apps.accounts.models import User
    from apps.alerts.models import NotificationPreference
    user = User.objects.filter(email=f"victim{org.pk}@iso.test").first() or User.objects.create_user(
        email=f"victim{org.pk}@iso.test", password="Victim-Pass-2026!")
    return NotificationPreference.unscoped.create(organization=org, user=user, channel="email").pk


def _channel(org: Organization) -> int:
    from apps.alerts.models import NotificationChannel
    return NotificationChannel.unscoped.create(organization=org, kind="email", provider="smtp").pk


def _command(org: Organization) -> int:
    from django.utils import timezone

    from apps.commands.models import Command
    return Command.unscoped.create(organization=org, device=Device_for(org), name="secret", command_text="getinfo",
                                   expires_at=timezone.now()).pk


def _report_job(org: Organization) -> int:
    from apps.reports.models import ReportJob
    return ReportJob.unscoped.create(organization=org, report="trips", format="csv").pk


def _schedule(org: Organization) -> int:
    from django.utils import timezone

    from apps.reports.models import ScheduledReport
    return ScheduledReport.unscoped.create(organization=org, name="Secret schedule", report="trips",
                                           frequency="daily", recipients=["a@b.test"], next_run_at=timezone.now()).pk


def _share(org: Organization) -> int:
    from django.utils import timezone

    from apps.fleet.models import Vehicle
    from apps.sharing.models import ShareLink
    v = Vehicle.unscoped.create(organization=org, registration_number=f"SHR {org.pk}")
    return ShareLink.unscoped.create(organization=org, vehicle=v, token_hash=f"{org.pk:064d}",
                                     expires_at=timezone.now()).pk


def _webhook(org: Organization) -> int:
    from apps.integrations.models import Webhook
    return Webhook.unscoped.create(organization=org, name="Secret hook", url="https://example.com/hook",
                                   events=["trip.completed"], secret="whsec_x").pk


def _api_key(org: Organization) -> int:
    from apps.accounts.models import User
    from apps.integrations.models import ApiKey
    user = User.objects.filter(email=f"victim{org.pk}@iso.test").first() or User.objects.create_user(
        email=f"victim{org.pk}@iso.test", password="Victim-Pass-2026!")
    return ApiKey.unscoped.create(organization=org, name="Secret key", prefix="gpk_x", key_hash=f"{org.pk:064d}",
                                  scopes=["vehicles.view"], created_by=user).pk


def _maintenance(org: Organization) -> int:
    from django.utils import timezone

    from apps.maintenance.models import MaintenanceSchedule
    return MaintenanceSchedule.unscoped.create(organization=org, vehicle_id=_vehicle(org), name="Secret service",
                                               interval_days=30, last_service_at=timezone.now()).pk


def _service(org: Organization) -> int:
    from django.utils import timezone

    from apps.maintenance.models import ServiceRecord
    return ServiceRecord.unscoped.create(organization=org, vehicle_id=_vehicle(org), title="Secret service",
                                         performed_at=timezone.now()).pk


def _first(model_path: str) -> Callable[[Organization], int]:
    def make(org: Organization) -> int:
        from django.apps import apps
        return apps.get_model(model_path).objects.order_by("pk").first().pk  # seeded by migrations
    return make


#: basename -> factory creating an object owned by the given organization (catalog: shared seeded rows)
FACTORIES: dict[str, Callable[[Organization], int]] = {
    "users": _membership,
    "roles": _role,
    "audit-logs": _audit,
    "organizations": lambda org: org.pk,
    "platform-audit-logs": _audit,
    "catalog-manufacturers": _first("device_catalog.DeviceManufacturer"),
    "catalog-protocols": _first("device_catalog.DeviceProtocol"),
    "catalog-models": _first("device_catalog.DeviceModel"),
    "catalog-command-templates": _first("device_catalog.CommandTemplate"),
    "catalog-io-profiles": _first("device_catalog.IoMappingProfile"),
    "catalog-listener-ports": _first("device_catalog.ListenerPort"),
    "catalog-generic-formats": _first("device_catalog.GenericFormat"),
    "sims": _sim,
    "vehicle-groups": _group,
    "vehicles": _vehicle,
    "drivers": _driver,
    "devices": _device,
    "platform-unregistered-devices": lambda org: _unregistered(),
    "tracking-live": _device,
    "dashboard-summary": _vehicle,
    "tracking-history": _vehicle,
    "trips": _analysis("analysis.Trip"),
    "stops": _analysis("analysis.Stop"),
    "idle-events": _analysis("analysis.IdleEvent"),
    "speed-events": _analysis("analysis.SpeedEvent"),
    "driving-events": _analysis("analysis.DrivingBehaviorEvent"),
    "geofence-events": _analysis("geofences.GeofenceEvent"),
    "geofences": _geofence,
    "alert-rules": _alert_rule,
    "alerts": _alert,
    "notifications": _notification,
    "notification-preferences": _preference,
    "notification-channels": _channel,
    "commands": _command,
    "reports": None,             # a catalogue of report types; data scoping is tested in apps/reports/tests
    "report-jobs": _report_job,
    "scheduled-reports": _schedule,
    "share-links": _share,
    "search": None,              # results are scoped per tenant; tested in apps/core/tests/test_insight.py
    "webhooks": _webhook,
    "api-keys": _api_key,
    "maintenance-schedules": _maintenance,
    "maintenance-records": _service,
    "analytics": _vehicle,       # aggregates ("summary" policy): another tenant's vehicles must not be counted
    "my-vehicle": None,          # only the caller's own assignment; tested in apps/tracking/tests/test_driver_portal.py
}


def _ids(resp: Any) -> set[int]:
    data = resp.json()
    rows = data["results"] if isinstance(data, dict) and "results" in data else data
    return {r["id"] for r in rows}


def test_every_route_is_covered():
    missing = [basename for _, _, basename in router.registry if basename not in FACTORIES]
    assert not missing, f"Add tenant-isolation factories for: {missing}"


@pytest.mark.parametrize("prefix,viewset,basename", router.registry)
def test_other_tenant_cannot_see_or_touch(prefix, viewset, basename, org_a, org_b, make_member, api):
    factory = FACTORIES[basename]
    if factory is None:          # no tenant rows behind this route (documented in FACTORIES)
        return
    victim_pk = factory(org_a)
    attacker = api(make_member(org_b, "org_admin", "attacker@b.test"))
    policy = viewset.tenant_policy
    url = f"/api/v1/{prefix}/"
    detail = f"{url}{victim_pk}/"
    listing = attacker.get(url)
    if policy == "platform":
        assert listing.status_code == 403
        assert attacker.get(detail).status_code == 403
        return
    if policy == "self":  # the user's own settings: never another tenant's rows
        body = listing.json()
        assert listing.status_code == 200 and all(p["id"] != victim_pk for p in body["preferences"]), basename
        return
    if policy == "history":  # queried by vehicle id: another tenant's vehicle must look missing
        window = "&start=2026-01-01T00:00:00Z&end=2026-01-02T00:00:00Z"
        for suffix in ("", "export/"):
            url = f"{reverse(f'{basename}-list')}{suffix}?vehicle={victim_pk}{window}&type=csv"
            status = attacker.get(url).status_code
            assert status == 404, f"{basename}{suffix}: org A vehicle answered {status}"
            assert attacker.get(url).json()["error"]["code"] == "not_found"   # our 404, not a routing artefact
        return
    if policy == "summary":  # aggregates: another tenant's objects must not be counted
        assert listing.status_code == 200 and listing.json()["vehicles"] == 0, f"{basename} counted org A data"
        return
    if policy == "catalog":  # shared platform data: readable by members, writable only by Super Administrators
        assert listing.status_code == 200 and attacker.get(detail).status_code == 200
        for method in ("patch", "delete"):
            status = getattr(attacker, method)(detail, {}, format="json").status_code
            assert status == 403, f"{basename}: {method} by an organization admin returned {status}"
        return
    assert listing.status_code == 200
    assert victim_pk not in _ids(listing), f"{basename}: org A object leaked into org B listing"
    assert attacker.get(detail).status_code == 404
    for method in ("patch", "delete"):
        status = getattr(attacker, method)(detail, {}, format="json").status_code
        assert status in (403, 404), f"{basename}: {method} on another tenant's object returned {status}"
    # Spoofing the organization header must not grant access either.
    spoof = api(OrganizationMembership.unscoped.get(user__email="attacker@b.test").user, org_a)
    assert spoof.get(url).status_code in (403, 404) or victim_pk not in _ids(spoof.get(url))


PUBLIC = {"api/v1/auth/login/", "api/v1/auth/refresh/", "api/v1/auth/logout/", "api/v1/auth/password/forgot/",
          "api/v1/auth/password/reset/",
          "api/v1/public/share/1/"}   # share links are public by design (spec A13); scoped by their secret token


def _api_paths(resolver: URLResolver | None = None, prefix: str = "") -> list[str]:
    out = []
    for p in (resolver or get_resolver()).url_patterns:
        route = prefix + str(p.pattern)
        if isinstance(p, URLResolver):
            out.extend(_api_paths(p, route))
        elif isinstance(p, URLPattern) and route.startswith("api/v1/"):
            out.append(route)
    return out


def _sample_url(route: str) -> str:
    import re
    url = route.replace("^", "").replace("$", "")
    url = re.sub(r"<uuid:\w+>", "00000000-0000-0000-0000-000000000001", url)
    url = re.sub(r"\(\?P<\w+>[^)]+\)|<[^>]+>", "1", url)
    return "/" + url


def test_every_api_route_requires_authentication(client):
    """Unauthenticated requests to every non-public route get 401 (spec A16 security tests)."""
    paths = [p for p in _api_paths() if "format" not in p]  # skip DRF ".json" suffix duplicates
    assert len(paths) > 20
    checked = 0
    for route in paths:
        url = _sample_url(route)
        if url.lstrip("/") in PUBLIC:
            continue
        resp = client.get(url)
        if resp.status_code == 405:
            resp = client.post(url, {}, content_type="application/json")
        assert resp.status_code == 401, f"{url} answered {resp.status_code} without authentication"
        checked += 1
    assert checked >= 20
