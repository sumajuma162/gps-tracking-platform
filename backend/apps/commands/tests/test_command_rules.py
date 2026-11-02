"""Command safety rules (spec A13): templates of the tracker's own model, commands capability, and for output
control: capability flag, hardware verification, dedicated permission, confirmation and audit."""
from __future__ import annotations

from unittest import mock

import pytest

from apps.audit.models import AuditLog
from apps.device_catalog.models import CommandTemplate, DeviceModel
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


@pytest.fixture
def device(org_a):
    return Device.unscoped.create(organization=org_a, name="C", identifier="356307042441013",
                                  device_model=DeviceModel.objects.get(slug="teltonika-fmc920"))


@pytest.fixture(autouse=True)
def no_redis():
    with mock.patch("apps.commands.views.publish"):
        yield


def post(client, device, template, **kw):  # type: ignore[no-untyped-def]
    return client.post("/api/v1/commands/", {"device": device.pk, "template": template.pk, **kw}, format="json")


def test_read_only_commands_and_permissions(org_a, make_member, api, device):
    getinfo = CommandTemplate.objects.get(device_model=device.device_model, command_text="getinfo")
    assert post(api(make_member(org_a, "viewer")), device, getinfo).status_code == 403
    ok = post(api(make_member(org_a, "org_admin")), device, getinfo)
    assert ok.status_code == 201 and ok.json()["status"] == "PENDING" and ok.json()["is_dangerous"] is False
    other = CommandTemplate.objects.create(device_model=DeviceModel.objects.get(slug="teltonika-fmb920"),
                                           name="X", command_text="getinfo")
    assert post(api(make_member(org_a, "org_admin", "a2@x.test")), device, other).json()["error"]["code"] == \
        "wrong_template"


def test_output_control_needs_every_safeguard(org_a, make_member, api, device):
    admin = api(make_member(org_a, "org_admin"))
    relay = CommandTemplate.objects.create(device_model=device.device_model, name="Immobilize", is_dangerous=True,
                                           command_text="setdigout 1", is_verified=False)
    assert post(admin, device, relay, confirm=True).json()["error"]["code"] == "capability_missing"
    model = device.device_model
    model.capabilities = {**model.capabilities, "output_control": True}
    model.save()
    assert post(admin, device, relay, confirm=True).json()["error"]["code"] == "unverified_dangerous"
    relay.is_verified = True
    relay.save()
    assert post(admin, device, relay).json()["error"]["code"] == "confirmation_required"
    fleet = api(make_member(org_a, "fleet_manager"))                    # can send, but not output control
    blocked = post(fleet, device, relay, confirm=True)
    assert blocked.status_code == 403 or blocked.json()["error"]["code"] in ("forbidden",)
    ok = post(admin, device, relay, confirm=True)
    assert ok.status_code == 201 and ok.json()["is_dangerous"] is True
    entry = AuditLog.objects.get(action="commands.queued", metadata__dangerous=True)
    assert entry.metadata["command"] == "Immobilize"


def test_disabled_trackers_get_no_commands(org_a, make_member, api, device):
    device.is_active = False
    device.save()
    getinfo = CommandTemplate.objects.get(device_model=device.device_model, command_text="getinfo")
    assert post(api(make_member(org_a, "org_admin")), device, getinfo).json()["error"]["code"] == "device_disabled"
