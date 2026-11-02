"""Row-Level Security verified inside PostgreSQL as the restricted runtime role."""
from __future__ import annotations

import pytest
from django.db import DatabaseError, connection, transaction

from apps.audit.models import AuditLog

pytestmark = pytest.mark.django_db


@pytest.mark.skipif("connection.vendor != 'postgresql'")
def test_rls_policies_enforced_for_runtime_role(org_a, org_b):
    a = AuditLog.objects.create(organization=org_a, action="a.event").pk
    AuditLog.objects.create(organization=org_b, action="b.event")
    AuditLog.objects.create(organization=None, action="platform.event")
    with connection.cursor() as cur:
        cur.execute("SET LOCAL ROLE gps_app")
        cur.execute("SELECT set_config('app.current_org', %s, true)", [str(org_a.pk)])
        cur.execute("SELECT action FROM audit_logs ORDER BY action")
        assert [r[0] for r in cur.fetchall()] == ["a.event"]          # B and platform rows invisible

        with pytest.raises(DatabaseError), transaction.atomic():    # cannot write into another tenant
            cur.execute("INSERT INTO audit_logs (organization_id, action, resource_type, resource_id, user_agent, "
                        "metadata, created_at) VALUES (%s, 'x', '', '', '', '{}', now())", [org_b.pk])
        with pytest.raises(DatabaseError), transaction.atomic():    # append-only: no UPDATE privilege
            cur.execute("UPDATE audit_logs SET action = 'tampered' WHERE id = %s", [a])
        with pytest.raises(DatabaseError), transaction.atomic():    # append-only: no DELETE privilege
            cur.execute("DELETE FROM audit_logs WHERE id = %s", [a])

        cur.execute("SELECT set_config('app.current_org', '', true)")
        cur.execute("SELECT count(*) FROM audit_logs")
        assert cur.fetchone()[0] == 0                                   # no tenant context -> nothing

        cur.execute("SELECT set_config('app.bypass_rls', 'on', true)")
        cur.execute("SELECT count(*) FROM audit_logs")
        assert cur.fetchone()[0] == 3                                   # explicit platform bypass
        cur.execute("RESET ROLE")


@pytest.mark.skipif("connection.vendor != 'postgresql'")
def test_runtime_role_is_not_superuser_and_cannot_bypass():
    with connection.cursor() as cur:
        cur.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'gps_app'")
        assert cur.fetchone() == (False, False)


@pytest.mark.skipif("connection.vendor != 'postgresql'")
def test_api_requests_work_as_runtime_role(org_a, make_member, api, settings):
    """A full API request with SET ROLE gps_app: RLS context is set and the audit view still works."""
    settings.DB_RUNTIME_ROLE = "gps_app"
    from apps.audit.services import record
    record("visible.event", organization=org_a)
    client = api(make_member(org_a, "org_admin"))
    with connection.cursor() as cur:
        cur.execute("SET ROLE gps_app")
    try:
        resp = client.get("/api/v1/audit-logs/")
        assert resp.status_code == 200
        assert "visible.event" in [r["action"] for r in resp.json()["results"]]
    finally:
        with connection.cursor() as cur:
            cur.execute("RESET ROLE")
