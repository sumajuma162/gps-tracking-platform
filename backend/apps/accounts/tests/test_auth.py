"""Authentication flows (spec A8): login, lockout, refresh rotation, CSRF, logout, passwords, sessions."""
from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.core import mail
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts import tokens
from apps.accounts.models import User, UserSession
from apps.accounts.views import consume_ws_ticket
from apps.audit.models import AuditLog
from apps.audit.services import record
from conftest import PASSWORD

pytestmark = pytest.mark.django_db
LOGIN = "/api/v1/auth/login/"


def login(client: APIClient, email: str, password: str = PASSWORD):  # type: ignore[no-untyped-def]
    return client.post(LOGIN, {"email": email, "password": password}, format="json")


def test_login_success_sets_secure_cookie_and_returns_memberships(org_a, make_member):
    make_member(org_a, "org_admin", "Admin@Demo.test")
    client = APIClient()
    resp = login(client, "admin@demo.test")  # email is case-insensitive
    assert resp.status_code == 200
    body = resp.json()
    assert body["access"] and body["user"]["email"] == "admin@demo.test"
    assert body["memberships"][0]["role"]["slug"] == "org_admin"
    assert "users.manage" in body["memberships"][0]["permissions"]
    cookie = resp.cookies["gps_refresh"]
    assert cookie["httponly"] and cookie["path"] == "/api/v1/auth/" and cookie["samesite"] == "Strict"
    assert "refresh" not in body  # refresh token is never exposed to JavaScript
    assert AuditLog.objects.filter(action="auth.login").count() == 1


def test_wrong_password_and_unknown_email_get_identical_response(org_a, make_member):
    make_member(org_a, "viewer", "v@demo.test")
    a = login(APIClient(), "v@demo.test", "wrong-password-123")
    b = login(APIClient(), "nobody@demo.test", "wrong-password-123")
    assert a.status_code == b.status_code == 401
    assert a.json()["error"] == b.json()["error"]
    assert AuditLog.objects.filter(action="auth.login_failed").count() == 2


def test_lockout_after_five_failures_then_expires(org_a, make_member):
    make_member(org_a, "viewer", "v@demo.test")
    for _ in range(5):
        assert login(APIClient(), "v@demo.test", "bad-password-xyz").status_code == 401
    assert login(APIClient(), "v@demo.test").status_code == 401  # correct password, but locked
    user = User.objects.get(email="v@demo.test")
    assert user.is_locked()
    assert AuditLog.objects.filter(action="auth.account_locked").exists()
    User.objects.filter(pk=user.pk).update(locked_until=timezone.now() - timedelta(seconds=1))
    assert login(APIClient(), "v@demo.test").status_code == 200
    user.refresh_from_db()
    assert user.failed_login_count == 0 and user.locked_until is None


def test_inactive_user_cannot_login(org_a, make_member):
    user = make_member(org_a, "viewer", "v@demo.test")
    User.objects.filter(pk=user.pk).update(is_active=False)
    assert login(APIClient(), "v@demo.test").status_code == 401


def test_refresh_rotates_and_detects_reuse(org_a, make_member):
    make_member(org_a, "viewer", "v@demo.test")
    client = APIClient()
    login(client, "v@demo.test")
    old = client.cookies["gps_refresh"].value
    r1 = client.post("/api/v1/auth/refresh/")
    assert r1.status_code == 200 and r1.json()["access"]
    new = client.cookies["gps_refresh"].value
    assert new != old
    attacker = APIClient()
    attacker.cookies["gps_refresh"] = old
    assert attacker.post("/api/v1/auth/refresh/").status_code == 401
    session = UserSession.objects.get()
    assert session.revoked_reason == "refresh_reuse"
    assert client.post("/api/v1/auth/refresh/").status_code == 401  # whole session killed
    assert AuditLog.objects.filter(action="auth.refresh_reuse_detected").exists()




def test_refresh_and_logout_enforce_csrf(org_a, make_member):
    make_member(org_a, "viewer", "v@demo.test")
    client = APIClient(enforce_csrf_checks=True)
    login(client, "v@demo.test")
    no_header = client.post("/api/v1/auth/refresh/")
    assert no_header.status_code == 403 and no_header.json()["error"]["code"] == "csrf_failed"
    csrf = client.cookies["csrftoken"].value
    assert client.post("/api/v1/auth/refresh/", HTTP_X_CSRFTOKEN=csrf).status_code == 200
    assert client.post("/api/v1/auth/logout/").status_code == 403
    assert client.post("/api/v1/auth/logout/", HTTP_X_CSRFTOKEN=csrf).status_code == 204


def test_logout_revokes_session_and_access_token(org_a, make_member):
    make_member(org_a, "viewer", "v@demo.test")
    client = APIClient()
    access = login(client, "v@demo.test").json()["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    assert client.get("/api/v1/auth/me/").status_code == 200
    assert client.post("/api/v1/auth/logout/").status_code == 204
    assert client.get("/api/v1/auth/me/").status_code == 401
    assert client.post("/api/v1/auth/refresh/").status_code == 401


def test_expired_and_wrong_type_tokens_rejected(org_a, make_member, api):
    user = make_member(org_a, "viewer", "v@demo.test")
    client = api(user)
    sid = str(client.session_obj.pk)
    expired = tokens.encode(user.pk, sid, "access", timedelta(seconds=-5))
    refresh_as_access = tokens.encode(user.pk, sid, "refresh", timedelta(minutes=5))
    for token in (expired, refresh_as_access, "garbage"):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        assert c.get("/api/v1/auth/me/").status_code == 401


def test_password_change(org_a, make_member, api):
    user = make_member(org_a, "viewer", "v@demo.test")
    current, other = api(user), api(user)
    url = "/api/v1/auth/password/change/"
    wrong = current.post(url, {"current_password": "nope", "new_password": "An0ther-Strong-Pass"}, format="json")
    assert wrong.json()["error"]["code"] == "current_password_wrong"
    weak = current.post(url, {"current_password": PASSWORD, "new_password": "password"}, format="json")
    assert weak.status_code == 400 and weak.json()["error"]["details"]["new_password"]
    ok = current.post(url, {"current_password": PASSWORD, "new_password": "An0ther-Strong-Pass"}, format="json")
    assert ok.status_code == 204
    assert current.get("/api/v1/auth/me/").status_code == 200   # this session continues
    assert other.get("/api/v1/auth/me/").status_code == 401     # other sessions ended
    meta = AuditLog.objects.get(action="auth.password_changed").metadata
    assert "An0ther" not in str(meta)


def test_forgot_and_reset_password(org_a, make_member, api):
    user = make_member(org_a, "viewer", "v@demo.test")
    old_session = api(user)
    anon = APIClient()
    assert anon.post("/api/v1/auth/password/forgot/", {"email": "nobody@x.test"}, format="json").status_code == 202
    assert len(mail.outbox) == 0
    assert anon.post("/api/v1/auth/password/forgot/", {"email": "v@demo.test"}, format="json").status_code == 202
    assert len(mail.outbox) == 1
    uid, token = re.search(r"uid=([^&]+)&token=(\S+)", mail.outbox[0].body).groups()
    payload = {"uid": uid, "token": token, "new_password": "Reset-Pass-2026-ok"}
    assert anon.post("/api/v1/auth/password/reset/", payload, format="json").status_code == 204
    assert old_session.get("/api/v1/auth/me/").status_code == 401
    again = anon.post("/api/v1/auth/password/reset/", payload, format="json")
    assert again.json()["error"]["code"] == "reset_link_invalid"  # single use
    assert login(APIClient(), "v@demo.test", "Reset-Pass-2026-ok").status_code == 200


def test_sessions_list_and_revoke_others(org_a, make_member, api):
    user = make_member(org_a, "viewer", "v@demo.test")
    a, b = api(user), api(user)
    rows = a.get("/api/v1/auth/sessions/").json()
    assert len(rows) == 2 and sum(r["current"] for r in rows) == 1
    assert a.post("/api/v1/auth/sessions/revoke-others/").json() == {"ended": 1}
    assert b.get("/api/v1/auth/me/").status_code == 401
    other_user = make_member(org_a, "auditor", "x@demo.test")
    foreign = str(api(other_user).session_obj.pk)
    assert a.delete(f"/api/v1/auth/sessions/{foreign}/").status_code == 404


def test_me_update_language(org_a, make_member, api):
    client = api(make_member(org_a, "viewer", "v@demo.test"))
    resp = client.patch("/api/v1/auth/me/", {"language": "sw", "email": "hacker@x.test"}, format="json")
    assert resp.status_code == 200
    assert resp.json()["user"]["language"] == "sw" and resp.json()["user"]["email"] == "v@demo.test"


def test_websocket_ticket_single_use(org_a, make_member, api):
    client = api(make_member(org_a, "viewer", "v@demo.test"))
    ticket = client.post("/api/v1/auth/ws-ticket/").json()["ticket"]
    assert consume_ws_ticket(ticket)["user_id"] is not None
    assert consume_ws_ticket(ticket) is None


def test_audit_redacts_secrets(org_a):
    record("test.event", organization=org_a, metadata={"password": "x", "nested": {"api_key": "k", "ok": 1},
                                                       "items": [{"token": "t"}]})
    meta = AuditLog.objects.get(action="test.event").metadata
    assert meta == {"password": "[redacted]", "nested": {"api_key": "[redacted]", "ok": 1},
                    "items": [{"token": "[redacted]"}]}


def test_audit_entries_are_append_only(org_a):
    record("test.event", organization=org_a)
    entry = AuditLog.objects.get(action="test.event")
    with pytest.raises(RuntimeError):
        entry.save()
    with pytest.raises(RuntimeError):
        entry.delete()


def test_login_is_throttled(org_a, make_member):
    from rest_framework.settings import api_settings
    make_member(org_a, "viewer", "v@demo.test")
    rates = {**api_settings.DEFAULT_THROTTLE_RATES, "login": "3/min"}
    with override_settings(REST_FRAMEWORK={**__import__("django.conf").conf.settings.REST_FRAMEWORK,
                                           "DEFAULT_THROTTLE_RATES": rates}):
        from rest_framework.throttling import ScopedRateThrottle
        ScopedRateThrottle.THROTTLE_RATES = rates
        try:
            codes = [login(APIClient(), "v@demo.test", "bad").status_code for _ in range(4)]
        finally:
            ScopedRateThrottle.THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES
    assert codes[:3] == [401, 401, 401] and codes[3] == 429


def test_auth_events_visible_to_each_organization_of_the_user(org_a, org_b, make_member):
    make_member(org_a, "viewer", "both@x.test")
    make_member(org_b, "viewer", "both@x.test")
    login(APIClient(), "both@x.test", "wrong-password-1")
    login(APIClient(), "both@x.test")
    for org in (org_a, org_b):
        actions = sorted(AuditLog.objects.filter(organization=org).values_list("action", flat=True))
        assert actions == ["auth.login", "auth.login_failed"]
    assert not AuditLog.objects.filter(organization__isnull=True, action="auth.login").exists()
