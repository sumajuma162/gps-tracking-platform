"""Development seed data (spec A17). Refuses to run when DEBUG is off."""
from __future__ import annotations

import os
import secrets
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

from apps.accounts.models import User
from apps.device_catalog.models import DeviceModel
from apps.devices.models import Device, DeviceAssignment
from apps.fleet.models import Driver, DriverAssignment, SimCard, Vehicle, VehicleGroup
from apps.organizations.models import Organization, OrganizationMembership
from apps.rbac.models import Role

# Demo fleet (spec A17). The first IMEI is the one in Teltonika's documentation, so the official sample packets
# can be sent to a demo stack. All demo trackers are flagged as simulated.
FLEET = [
    # org, registration, make, model, type, IMEI, SIM phone, driver (first, last, employee no.)
    ("demo-logistics", "T 123 ABC", "Isuzu", "FVR 34", "truck", "356307042441013", "+255712000101",
     ("Hamisi", "Juma", "DL-001")),
    ("demo-logistics", "T 456 XYZ", "Toyota", "Hilux", "pickup", "352093086403655", "+255712000102",
     ("Neema", "Kimaro", "DL-002")),
    ("demo-logistics", "T 789 DEF", "Scania", "R450", "truck", "353201234567896", "+255712000103",
     ("Baraka", "Mushi", "DL-003")),
    ("coastal-transport", "T 321 CTL", "Mitsubishi", "Fuso", "truck", "359632107452945", "+255754000201",
     ("Rehema", "Ali", "CT-001")),
]

ORGS = [("Demo Logistics Ltd", "demo-logistics"), ("Coastal Transport Ltd", "coastal-transport")]
USERS = [
    ("admin@demo.local", "Asha Mwakyusa", "demo-logistics", "org_admin"),
    ("fleet@demo.local", "Baraka Mushi", "demo-logistics", "fleet_manager"),
    ("monitor@demo.local", "Neema Kimaro", "demo-logistics", "monitoring_officer"),
    ("viewer@demo.local", "Juma Said", "demo-logistics", "viewer"),
    ("auditor@demo.local", "Grace Mollel", "demo-logistics", "auditor"),
    ("driver@demo.local", "Hamisi Juma", "demo-logistics", "driver"),
    ("admin@coastal.local", "Rehema Ali", "coastal-transport", "org_admin"),
]


class Command(BaseCommand):
    help = "Create demo organizations and one user per role (development only)."

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG:
            raise CommandError("seed_demo only runs with DEBUG=1. Never seed demo accounts in production.")
        if settings.DB_RUNTIME_ROLE:
            settings.DB_RUNTIME_ROLE = ""
            connections.close_all()
        password = os.environ.get("SEED_DEMO_PASSWORD") or secrets.token_urlsafe(12) + "!9a"
        generated = not os.environ.get("SEED_DEMO_PASSWORD")
        with transaction.atomic():
            orgs = {}
            for name, slug in ORGS:
                orgs[slug], _ = Organization.objects.get_or_create(
                    slug=slug, defaults={"name": name, "timezone": "Africa/Dar_es_Salaam"})
            admin, created = User.objects.get_or_create(email="superadmin@example.com", defaults={
                "full_name": "Platform Administrator", "is_platform_admin": True, "is_staff": True,
                "is_superuser": True})
            if created:
                admin.set_password(password)
                admin.save()
            for email, full_name, org_slug, role_slug in USERS:
                user, created = User.objects.get_or_create(email=email, defaults={"full_name": full_name})
                if created:
                    user.set_password(password)
                    user.save()
                OrganizationMembership.unscoped.get_or_create(
                    organization=orgs[org_slug], user=user,
                    defaults={"role": Role.objects.get(slug=role_slug, organization__isnull=True)})
            self._seed_fleet(orgs)
            self._seed_extras(orgs)
        self.stdout.write(self.style.SUCCESS(
            "Demo data ready: 2 organizations, 8 users, 6 vehicles with trackers (4 FMC920, 1 GT06, 1 HTTP), "
            "2 geofences, 3 alert rules. Generate history with: python -m gpssim --backfill-hours 168"))
        if generated:
            self.stdout.write(f"Password for newly created demo users (shown once): {password}")
        else:
            self.stdout.write("Newly created demo users use SEED_DEMO_PASSWORD.")

    def _seed_extras(self, orgs: dict[str, Organization]) -> None:
        """Spec A17: a depot circle and a port polygon with alert rules (Demo Logistics), and one GT06 and one
        generic HTTP tracker in Coastal Transport."""
        from django.utils import timezone

        from apps.alerts.models import AlertRule
        from apps.geofences.models import Geofence

        demo, coastal = orgs["demo-logistics"], orgs["coastal-transport"]
        # driver@demo.local signs in to the driver portal as Hamisi Juma (T 123 ABC).
        Driver.unscoped.filter(organization=demo, employee_number="DL-001", user__isnull=True).update(
            user=User.objects.filter(email="driver@demo.local").first())
        Geofence.unscoped.get_or_create(organization=demo, name="Depot", deleted_at=None, defaults={
            "kind": "circle", "center_lat": -6.8235, "center_lon": 39.2695, "radius_m": 300,
            "description": "Main yard, Kurasini", "color": "#16a34a"})
        Geofence.unscoped.get_or_create(organization=demo, name="Port of Dar es Salaam", deleted_at=None, defaults={
            "kind": "polygon", "polygon": [[-6.8300, 39.2850], [-6.8300, 39.2960], [-6.8420, 39.2960],
                                           [-6.8420, 39.2850]],
            "description": "Port area (approximate)", "color": "#2563eb", "dwell_minutes": 120})
        for name, kind, params, severity in (
                ("Speeding over 80 km/h", "overspeed", {"limit_kmh": 80}, "warning"),
                ("Left the depot", "geofence_exit", {}, "info"),
                ("Entered the port", "geofence_enter", {}, "info")):
            AlertRule.unscoped.get_or_create(organization=demo, name=name, defaults={
                "type": kind, "params": params, "severity": severity, "applies_to_all": True,
                "recipient_roles": ["org_admin", "fleet_manager", "monitoring_officer"]})
        now = timezone.now()
        for reg, model_slug, identifier, name in (("T 777 CTL", "gt06-generic", "868120145233604", "GT06 T 777 CTL"),
                                                  ("T 888 CTL", "generic-http-json", "COASTAL-HTTP-1",
                                                   "HTTP tracker T 888 CTL")):
            model = DeviceModel.objects.filter(slug=model_slug).first()
            if model is None or Device.unscoped.filter(identifier=identifier, deleted_at__isnull=True).exists():
                continue
            vehicle, _ = Vehicle.unscoped.get_or_create(organization=coastal, registration_number=reg,
                                                        deleted_at=None, defaults={"make": "Isuzu", "model": "NQR"})
            device = Device.unscoped.create(organization=coastal, name=name, identifier=identifier,
                                            device_model=model, inventory_status="installed", is_simulated=True)
            DeviceAssignment.unscoped.create(organization=coastal, device=device, vehicle=vehicle, assigned_at=now)

    def _seed_fleet(self, orgs: dict[str, Organization]) -> None:
        from django.utils import timezone

        model = DeviceModel.objects.get(slug="teltonika-fmc920")
        now = timezone.now()
        upcountry, _ = VehicleGroup.unscoped.get_or_create(organization=orgs["demo-logistics"], name="Upcountry routes",
                                                           defaults={"description": "Dar es Salaam to Morogoro"})
        for org_slug, reg, make, model_name, vtype, imei, phone, (first, last, emp) in FLEET:
            org = orgs[org_slug]
            vehicle, created = Vehicle.unscoped.get_or_create(
                organization=org, registration_number=reg, deleted_at=None,
                defaults={"make": make, "model": model_name, "vehicle_type": vtype})
            if created and org_slug == "demo-logistics":
                vehicle.groups.add(upcountry)
            sim, _ = SimCard.unscoped.get_or_create(organization=org, phone_number=phone, deleted_at=None,
                                                    defaults={"operator": "Vodacom", "apn": "internet"})
            if Device.unscoped.filter(identifier=imei, deleted_at__isnull=True).exists():
                continue
            device = Device.unscoped.create(organization=org, name=f"FMC920 {reg}", identifier=imei, device_model=model,
                                            sim=sim, inventory_status="installed", installation_date=now.date(),
                                            is_simulated=True)
            DeviceAssignment.unscoped.create(organization=org, device=device, vehicle=vehicle, assigned_at=now)
            driver, _ = Driver.unscoped.get_or_create(organization=org, employee_number=emp, deleted_at=None,
                                                      defaults={"first_name": first, "last_name": last})
            if not DriverAssignment.unscoped.filter(vehicle=vehicle, unassigned_at__isnull=True).exists():
                DriverAssignment.unscoped.create(organization=org, driver=driver, vehicle=vehicle, assigned_at=now)
