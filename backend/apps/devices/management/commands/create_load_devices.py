"""Register simulated trackers for the load test (spec A16 load mode). Refuses to run with DEBUG off unless
``--force`` is given, and marks every tracker ``is_simulated``.

    python manage.py create_load_devices --count 2000 [--organization demo-logistics]
"""
from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.device_catalog.models import DeviceModel
from apps.devices.models import Device
from apps.organizations.models import Organization
from common.rls import rls_bypass

LOAD_PREFIX = "35999"   # keep in step with simulator/gpssim/load.py (tested)


def luhn_digit(body: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch) * (2 if i % 2 == 0 else 1)
        total += d - 9 if d > 9 else d
    return str((10 - total % 10) % 10)


def load_imei(index: int) -> str:
    body = f"{LOAD_PREFIX}{index:09d}"
    return body + luhn_digit(body)


class Command(BaseCommand):
    help = "Create simulated FMC920 trackers whose IMEIs match the simulator's load mode."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--count", type=int, default=1000)
        parser.add_argument("--organization", default="demo-logistics", help="organization slug")
        parser.add_argument("--force", action="store_true", help="allow with DEBUG off (staging load tests)")

    def handle(self, *args: Any, count: int, organization: str, force: bool, **opts: Any) -> None:
        if not settings.DEBUG and not force:
            raise CommandError("Refusing to create simulated trackers with DEBUG off (use --force on staging).")
        org = Organization.objects.filter(slug=organization).first()
        if org is None:
            raise CommandError(f"No organization with slug {organization!r}.")
        model = DeviceModel.objects.get(slug="teltonika-fmc920")
        wanted = {load_imei(i) for i in range(count)}
        with transaction.atomic(), rls_bypass():
            existing = set(Device.unscoped.filter(identifier__in=wanted).values_list("identifier", flat=True))
            Device.unscoped.bulk_create([
                Device(organization=org, device_model=model, identifier=imei, name=f"Load {imei[-6:]}",
                       is_simulated=True, inventory_status="installed")
                for imei in sorted(wanted - existing)], batch_size=1000)
        self.stdout.write(f"{len(wanted - existing)} created, {len(existing)} already registered.")
