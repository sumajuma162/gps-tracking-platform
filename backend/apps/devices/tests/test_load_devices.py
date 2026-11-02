"""The load-test command registers exactly the IMEIs the simulator's load mode connects with (spec A16)."""
from __future__ import annotations

import pytest
from django.core.management import call_command

from apps.devices.management.commands.create_load_devices import load_imei
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


def test_same_imeis_as_the_simulator_and_idempotent(org_a, settings):
    gpssim_load = pytest.importorskip("gpssim.load", reason="add ../simulator to PYTHONPATH")
    assert [load_imei(i) for i in (0, 7, 2999)] == [gpssim_load.load_imei(i) for i in (0, 7, 2999)]
    settings.DEBUG = True
    call_command("create_load_devices", count=5, organization=org_a.slug)
    call_command("create_load_devices", count=5, organization=org_a.slug)
    assert Device.unscoped.filter(is_simulated=True, organization=org_a).count() == 5
    settings.DEBUG = False
    with pytest.raises(Exception, match="DEBUG off"):
        call_command("create_load_devices", count=1, organization=org_a.slug)
