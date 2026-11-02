"""Audit log search (spec A13: append-only and searchable in the UI)."""
from __future__ import annotations

import pytest

from apps.audit.models import AuditLog

pytestmark = pytest.mark.django_db


def test_search_filters(org_a, org_b, make_member, api):
    AuditLog.objects.create(organization=org_a, action="vehicles.updated", resource_type="fleet.vehicle",
                            resource_id="7", ip_address="203.0.113.5", metadata={"changed": ["registration_number"]})
    AuditLog.objects.create(organization=org_a, action="geofences.created", resource_type="geofences.geofence",
                            resource_id="3", metadata={"label": "Port of Dar"})
    AuditLog.objects.create(organization=org_b, action="geofences.created", metadata={"label": "Port of Dar"})
    client = api(make_member(org_a, "auditor"))

    def ids(**params: str) -> list[str]:
        return [e["action"] for e in client.get("/api/v1/audit-logs/", params).json()["results"]]
    assert ids(q="port of dar") == ["geofences.created"]                 # metadata text, this org only
    assert ids(resource_type="fleet.vehicle", resource_id="7") == ["vehicles.updated"]
    assert ids(ip="203.0.113.5") == ["vehicles.updated"]
    facets = client.get("/api/v1/audit-logs/facets/").json()
    assert "geofences.created" in facets["actions"] and "fleet.vehicle" in facets["resource_types"]
