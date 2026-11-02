"""Clone copies the IO profile; profile preview; backend preview matches the gateway's mapper."""
from __future__ import annotations

from unittest import mock

import pytest
from django.db import connection  # noqa: F401  (used by skipif)

from apps.device_catalog.mapping import apply_mappings
from apps.device_catalog.models import DeviceModel, IoMappingProfile

pytestmark = pytest.mark.django_db


def test_clone_gets_its_own_io_profile(api, superadmin):
    root = api(superadmin)
    fmc920 = DeviceModel.objects.get(slug="teltonika-fmc920")
    with mock.patch("apps.devices.events._publish"):
        body = root.post(f"/api/v1/device-catalog/models/{fmc920.pk}/clone/",
                         {"name": "FMB920", "slug": "teltonika-fmb920-v2"}, format="json").json()
    assert body["io_profile"] != fmc920.io_profile_id
    copy = IoMappingProfile.objects.get(pk=body["io_profile"])
    assert copy.current_version.mappings == fmc920.io_profile.current_version.mappings
    assert copy.verified is False and copy.current_version.version == 1
    edited = [dict(m, scale=0.01) if m["io_id"] == 66 else m for m in copy.current_version.mappings]
    with mock.patch("apps.devices.events._publish"):
        assert root.patch(f"/api/v1/device-catalog/io-profiles/{copy.pk}/", {"mappings": edited},
                          format="json").status_code == 200
    fmc920.io_profile.refresh_from_db()
    original = next(m for m in fmc920.io_profile.current_version.mappings if m["io_id"] == 66)
    assert original["scale"] == 0.001                                  # the original model is untouched


def test_profile_edit_tells_the_gateway(api, superadmin, django_capture_on_commit_callbacks):
    profile = IoMappingProfile.objects.get(slug="teltonika-fmc920")
    with mock.patch("apps.devices.events._publish") as pub, django_capture_on_commit_callbacks(execute=True):
        api(superadmin).patch(f"/api/v1/device-catalog/io-profiles/{profile.pk}/",
                              {"mappings": profile.current_version.mappings}, format="json")
    assert pub.call_args.args[0]["identifier"] == "*"


@pytest.mark.skipif("connection.vendor != 'postgresql'", reason="needs PostgreSQL views")
def test_preview_uses_sample_when_no_data_and_shows_the_difference(api, superadmin):
    profile = IoMappingProfile.objects.get(slug="teltonika-fmc920")
    draft = [dict(m, scale=0.01) if m["io_id"] == 66 else m for m in profile.current_version.mappings]
    body = api(superadmin).post(f"/api/v1/device-catalog/io-profiles/{profile.pk}/preview/", {"mappings": draft},
                                format="json").json()
    assert body["source"] == "sample"
    assert body["current"]["fields"]["external_voltage"] == pytest.approx(12.65)
    assert body["draft"]["fields"]["external_voltage"] == pytest.approx(126.5)
    bad = api(superadmin).post(f"/api/v1/device-catalog/io-profiles/{profile.pk}/preview/",
                               {"mappings": [{"io_id": 1, "kind": "field", "target": "nope"}]}, format="json")
    assert bad.status_code == 400


def test_preview_matches_gateway():
    """Contract: the editor's preview and the gateway's normalization agree for the same IO values."""
    gw = pytest.importorskip("gpsgw.normalization.io_mapper")
    from datetime import UTC, datetime

    from gpsgw.protocols.base import RawRecord

    profile = IoMappingProfile.objects.get(slug="teltonika-fmc920")
    mappings = profile.current_version.mappings
    io = {239: 1, 240: 0, 21: 4, 66: 12650, 67: 4012, 16: 123456, 182: 9, 69: 1, 253: 2, 247: 1, 9999: 5}
    raw = RawRecord(device_time=datetime(2026, 9, 1, tzinfo=UTC), latitude=-6.8, longitude=39.2, satellites=9, io=io)
    n = gw.normalize(raw, identifier="x", protocol="teltonika",
                     profile=gw.IoMappingProfile.model_validate({"name": "p", "mappings": mappings}),
                     server_time=datetime(2026, 9, 1, 1, tzinfo=UTC))
    preview = apply_mappings(mappings, {str(k): v for k, v in io.items()})
    for field, value in preview["fields"].items():
        assert getattr(n, field) == pytest.approx(value), field
    assert preview["sensors"] == pytest.approx(n.sensors)
    assert sorted(preview["events"]) == sorted(n.events)
    assert preview["unmapped"] == {"9999": 5}


def test_model_search(api, superadmin):
    url = "/api/v1/device-catalog/models/?search="
    slugs = [m["slug"] for m in api(superadmin).get(url + "fmc9").json()["results"]]
    assert slugs == ["teltonika-fmc920"]
    names = {m["name"] for m in api(superadmin).get(url + "teltonika").json()["results"]}
    assert {"FMC920", "FMB920", "FMC130"} <= names
