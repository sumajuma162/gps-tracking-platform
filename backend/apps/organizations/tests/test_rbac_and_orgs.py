"""Role permissions, user management, organizations and break-glass (spec A8)."""
from __future__ import annotations

import pytest
from django.core import mail

from apps.audit.models import AuditLog
from apps.organizations.models import OrganizationMembership
from apps.rbac.models import Role

pytestmark = pytest.mark.django_db
USERS = "/api/v1/users/"


def role_id(slug: str) -> int:
    return Role.objects.get(slug=slug, organization__isnull=True).pk


@pytest.mark.parametrize("role,can_list,can_invite,can_read_audit", [
    ("org_admin", True, True, True),
    ("fleet_manager", True, False, False),
    ("monitoring_officer", False, False, False),
    ("viewer", False, False, False),
    ("auditor", True, False, True),
    ("driver", False, False, False),
])
def test_role_matrix(org_a, make_member, api, role, can_list, can_invite, can_read_audit):
    client = api(make_member(org_a, role))
    assert (client.get(USERS).status_code == 200) is can_list
    invite = client.post(USERS, {"email": f"new-{role}@x.test", "role_id": role_id("viewer")}, format="json")
    assert (invite.status_code == 201) is can_invite
    assert (client.get("/api/v1/audit-logs/").status_code == 200) is can_read_audit


def test_invite_new_and_existing_user(org_a, org_b, make_member, api):
    admin = api(make_member(org_a, "org_admin"))
    resp = admin.post(USERS, {"email": "New.Person@x.test", "full_name": "New Person",
                              "role_id": role_id("fleet_manager")}, format="json")
    assert resp.status_code == 201 and resp.json()["user"]["email"] == "new.person@x.test"
    assert "reset-password" in mail.outbox[0].body and "invite=1" in mail.outbox[0].body
    dup = admin.post(USERS, {"email": "new.person@x.test", "role_id": role_id("viewer")}, format="json")
    assert dup.json()["error"]["code"] == "already_member"
    entry = AuditLog.objects.get(action="users.added")
    assert entry.organization_id == org_a.pk and entry.metadata["role"] == "fleet_manager"


def test_cannot_assign_another_organizations_custom_role(org_a, org_b, make_member, api):
    foreign = Role.objects.create(organization=org_b, slug="b-only", name="B only")
    admin = api(make_member(org_a, "org_admin"))
    resp = admin.post(USERS, {"email": "z@x.test", "role_id": foreign.pk}, format="json")
    assert resp.status_code == 400 and "role_id" in resp.json()["error"]["details"]


def test_role_change_and_last_admin_guard(org_a, make_member, api):
    admin_user = make_member(org_a, "org_admin")
    admin = api(admin_user)
    own = OrganizationMembership.unscoped.get(user=admin_user)
    resp = admin.patch(f"{USERS}{own.pk}/", {"role_id": role_id("viewer")}, format="json")
    assert resp.json()["error"]["code"] == "last_admin"
    assert admin.delete(f"{USERS}{own.pk}/").json()["error"]["code"] == "last_admin"
    second = make_member(org_a, "fleet_manager", "second@x.test")
    m2 = OrganizationMembership.unscoped.get(user=second)
    assert admin.patch(f"{USERS}{m2.pk}/", {"role_id": role_id("org_admin")}, format="json").status_code == 200
    assert admin.patch(f"{USERS}{own.pk}/", {"role_id": role_id("viewer")}, format="json").status_code == 200
    log = AuditLog.objects.filter(action="users.role_changed").order_by("id").first()
    assert log.metadata["from"] == "fleet_manager" and log.metadata["to"] == "org_admin"


def test_deactivate_and_reactivate_member(org_a, make_member, api):
    admin = api(make_member(org_a, "org_admin"))
    viewer_user = make_member(org_a, "viewer")
    viewer = api(viewer_user)
    m = OrganizationMembership.unscoped.get(user=viewer_user)
    assert admin.delete(f"{USERS}{m.pk}/").status_code == 204
    assert viewer.get("/api/v1/roles/").json()["error"]["code"] == "no_organization"
    assert admin.post(f"{USERS}{m.pk}/reactivate/").status_code == 200
    assert viewer.get("/api/v1/roles/").status_code == 200


def test_custom_roles(org_a, make_member, api):
    admin = api(make_member(org_a, "org_admin"))
    created = admin.post("/api/v1/roles/", {"name": "Night dispatcher",
                                            "permissions": ["tracking.view_live", "alerts.view"]}, format="json")
    assert created.status_code == 201 and created.json()["permission_codes"] == ["alerts.view", "tracking.view_live"]
    rid = created.json()["id"]
    bad = admin.post("/api/v1/roles/", {"name": "X", "permissions": ["root.everything"]}, format="json")
    assert bad.status_code == 400
    assert admin.patch(f"/api/v1/roles/{role_id('viewer')}/", {"name": "Hacked"}, format="json").status_code == 403
    user = make_member(org_a, "viewer", "dispatch@x.test")
    OrganizationMembership.unscoped.filter(user=user).update(role_id=rid)
    assert admin.delete(f"/api/v1/roles/{rid}/").json()["error"]["code"] == "role_in_use"
    upd = admin.patch(f"/api/v1/roles/{rid}/", {"permissions": ["alerts.view", "users.view"]}, format="json")
    assert upd.status_code == 200
    from conftest import client_for
    assert client_for(user).get(USERS).status_code == 200  # new permission effective immediately


def test_organization_management(org_a, org_b, make_member, api, superadmin):
    root = api(superadmin)
    created = root.post("/api/v1/organizations/", {"name": "Lake Freight Ltd", "timezone": "Africa/Nairobi",
                                                   "admin_email": "boss@lake.test"}, format="json")
    assert created.status_code == 201 and created.json()["slug"] == "lake-freight-ltd"
    assert OrganizationMembership.unscoped.get(user__email="boss@lake.test").role.slug == "org_admin"
    bad_tz = root.post("/api/v1/organizations/", {"name": "X", "timezone": "Mars/Base"}, format="json")
    assert bad_tz.status_code == 400

    admin = api(make_member(org_a, "org_admin"))
    assert admin.post("/api/v1/organizations/", {"name": "Mine"}, format="json").status_code == 403
    assert [o["id"] for o in admin.get("/api/v1/organizations/").json()["results"]] == [org_a.pk]
    assert admin.patch(f"/api/v1/organizations/{org_a.pk}/", {"timezone": "Africa/Kampala",
                                                              "is_active": False}, format="json").status_code == 200
    org_a.refresh_from_db()
    assert org_a.timezone == "Africa/Kampala" and org_a.is_active  # is_active ignored for non-platform users
    fleet = api(make_member(org_a, "fleet_manager"))
    assert fleet.patch(f"/api/v1/organizations/{org_a.pk}/", {"name": "X"}, format="json").status_code == 403
    assert root.delete(f"/api/v1/organizations/{org_b.pk}/").status_code == 204
    org_b.refresh_from_db()
    assert org_b.is_active is False


def test_break_glass_is_read_only_time_limited_and_visible(org_a, make_member, api, superadmin):
    make_member(org_a, "org_admin", "admin@a.test")
    root = api(superadmin, org_a)
    assert root.get(USERS).status_code == 404  # no membership, no grant
    short = root.post(f"/api/v1/organizations/{org_a.pk}/break-glass/", {"reason": "pls"}, format="json")
    assert short.status_code == 400
    grant = root.post(f"/api/v1/organizations/{org_a.pk}/break-glass/",
                      {"reason": "Customer ticket #4521: tracker offline"}, format="json")
    assert grant.status_code == 201 and grant.json()["access"] == "read-only"
    assert root.get(USERS).status_code == 200
    assert root.post(USERS, {"email": "x@y.test", "role_id": role_id("viewer")}, format="json").status_code == 403
    org_admin = api(OrganizationMembership.unscoped.get(user__email="admin@a.test").user)
    actions = [e["action"] for e in org_admin.get("/api/v1/audit-logs/").json()["results"]]
    assert "platform.break_glass_granted" in actions
    from django.utils import timezone

    from apps.organizations.models import BreakGlassGrant
    BreakGlassGrant.objects.update(expires_at=timezone.now())
    assert root.get(USERS).status_code == 404


def test_organization_selection(org_a, org_b, make_member, api):
    from conftest import client_for
    user = make_member(org_a, "viewer", "multi@x.test")
    make_member(org_b, "auditor", "multi@x.test")
    assert client_for(user).get("/api/v1/roles/").json()["error"]["code"] == "organization_required"
    assert client_for(user, org_b).get(USERS).status_code == 200      # auditor in B
    assert client_for(user, org_a).get(USERS).status_code == 403      # viewer in A
    bad = client_for(user)
    bad.credentials(HTTP_AUTHORIZATION=bad._credentials["HTTP_AUTHORIZATION"], HTTP_X_ORGANIZATION_ID="abc")
    assert bad.get(USERS).json()["error"]["code"] == "organization_invalid"


def test_permission_catalog(org_a, make_member, api):
    rows = api(make_member(org_a, "viewer")).get("/api/v1/permissions/").json()
    assert {"codename": "users.manage", "description": "Invite users and change roles"} in rows
