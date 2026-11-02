"""Development seed data (spec A17): refuses without DEBUG, idempotent, covers every role and both tenants."""
from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command

from apps.alerts.models import AlertRule
from apps.devices.models import Device
from apps.geofences.models import Geofence
from apps.organizations.models import OrganizationMembership

pytestmark = pytest.mark.django_db


def test_seed_demo(settings, monkeypatch):
    settings.DEBUG = False
    with pytest.raises(CommandError):
        call_command("seed_demo")
    settings.DEBUG = True
    monkeypatch.setenv("SEED_DEMO_PASSWORD", "Seed-Pass-2026!x")
    call_command("seed_demo")
    call_command("seed_demo")                                   # idempotent
    assert OrganizationMembership.unscoped.count() == 7
    models = sorted(Device.unscoped.values_list("device_model__slug", flat=True))
    assert models.count("teltonika-fmc920") == 4 and "gt06-generic" in models and "generic-http-json" in models
    assert Device.unscoped.filter(organization__slug="coastal-transport", device_model__slug="gt06-generic").exists()
    assert Geofence.unscoped.count() == 2 and AlertRule.unscoped.count() == 3
    assert all(Device.unscoped.values_list("is_simulated", flat=True))
    from apps.fleet.models import Driver
    assert Driver.unscoped.get(employee_number="DL-001").user.email == "driver@demo.local"   # driver portal
