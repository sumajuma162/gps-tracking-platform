"""Webhooks (signed, retried, logged) and API keys (scoped, revocable) — spec A13, A16 "webhooks signing"."""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.fleet.models import Vehicle
from apps.integrations import dispatch
from apps.integrations.events import emit
from apps.integrations.models import ApiKey, Webhook, WebhookDelivery, WebhookEvent

pytestmark = pytest.mark.django_db


class Receiver:
    """A local HTTP endpoint that records requests and answers with the queued status codes."""

    def __init__(self) -> None:
        self.requests: list[tuple[dict[str, str], bytes]] = []
        self.statuses: list[int] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.requests.append(({k.lower(): v for k, v in self.headers.items()}, body))
                code = outer.statuses.pop(0) if outer.statuses else 200
                self.send_response(code)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args: object) -> None:
                return None

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture
def receiver():  # type: ignore[no-untyped-def]
    r = Receiver()
    yield r
    r.close()


@pytest.fixture
def admin(org_a, make_member, api):  # type: ignore[no-untyped-def]
    return api(make_member(org_a, "org_admin"))


def verify(secret: str, headers: dict[str, str], body: bytes) -> bool:
    parts = dict(p.split("=", 1) for p in headers["x-gps-signature"].split(","))
    expected = hmac.new(secret.encode(), f"{parts['t']}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts["v1"])


def test_webhook_is_signed_retried_and_logged(org_a, admin, receiver):
    created = admin.post("/api/v1/webhooks/", {"name": "ERP", "url": receiver.url,
                                               "events": ["trip.completed", "alert.created"]}, format="json")
    assert created.status_code == 201, created.json()
    secret = created.json()["secret"]
    assert secret.startswith("whsec_")
    assert "secret" not in admin.get(f"/api/v1/webhooks/{created.json()['id']}/").json()   # shown once only
    audit = AuditLog.objects.get(action="webhooks.created")
    assert "secret" not in json.dumps(audit.metadata)

    assert emit(org_a.pk, "trip.completed", "trip:1:2026", {"vehicle_id": None, "distance_km": 12.5})
    assert not emit(org_a.pk, "trip.completed", "trip:1:2026", {"distance_km": 12.5})          # deduplicated
    emit(org_a.pk, "device.offline", "offline:1", {})                                           # not subscribed
    receiver.statuses = [503]                       # first attempt fails, the retry succeeds
    from apps.integrations.tasks import dispatch_webhooks
    first = dispatch_webhooks()
    assert first.pop("created") == 1
    assert first == {"succeeded": 0, "failed": 0, "retrying": 1}
    d = WebhookDelivery.unscoped.get()
    assert d.response_status == 503 and d.next_attempt_at > timezone.now() + timedelta(seconds=50)
    WebhookDelivery.unscoped.filter(pk=d.pk).update(next_attempt_at=timezone.now())
    assert dispatch.deliver_due()["succeeded"] == 1
    d.refresh_from_db()
    assert (d.status, d.attempts, d.response_status) == ("succeeded", 2, 200)
    headers, body = receiver.requests[-1]
    assert verify(secret, headers, body) and not verify("wrong", headers, body)
    payload = json.loads(body)
    assert payload["event"] == "trip.completed" and payload["data"]["distance_km"] == 12.5
    assert headers["x-gps-event"] == "trip.completed"
    log = admin.get(f"/api/v1/webhooks/{created.json()['id']}/deliveries/").json()["results"]
    assert log[0]["status"] == "succeeded" and log[0]["attempts"] == 2


def test_client_errors_are_not_retried_and_test_button(org_a, admin, receiver):
    hook = admin.post("/api/v1/webhooks/", {"name": "x", "url": receiver.url, "events": ["alert.created"]},
                      format="json").json()
    emit(org_a.pk, "alert.created", "alert:9", {"type": "crash"})
    dispatch.fan_out()
    receiver.statuses = [410]
    assert dispatch.deliver_due()["failed"] == 1                     # 4xx (except 408/429): final
    receiver.statuses = [200]
    test = admin.post(f"/api/v1/webhooks/{hook['id']}/test/").json()
    assert test["status"] == "succeeded" and test["event"] == "webhook.test"
    assert json.loads(receiver.requests[-1][1])["event"] == "webhook.test"


def test_webhook_url_must_be_public_https(admin, settings):
    settings.WEBHOOK_ALLOW_HTTP = False
    settings.WEBHOOK_ALLOW_PRIVATE = False
    for url in ("http://example.com/hook", "https://127.0.0.1/hook", "https://10.1.2.3/x", "ftp://example.com"):
        resp = admin.post("/api/v1/webhooks/", {"name": "x", "url": url, "events": ["alert.created"]}, format="json")
        assert resp.status_code == 400, url
    with pytest.raises(dispatch.UnsafeUrl):
        dispatch.check_url("https://169.254.169.254/latest/meta-data")    # cloud metadata endpoint
    bad = admin.post("/api/v1/webhooks/", {"name": "x", "url": "https://example.com", "events": ["nope"]},
                     format="json")
    assert bad.status_code == 400


def test_webhooks_need_integrations_permission(org_a, make_member, api):
    assert api(make_member(org_a, "fleet_manager")).get("/api/v1/webhooks/").status_code == 403


def test_api_key_scopes_revocation_and_limits(org_a, org_b, admin, make_member):
    Vehicle.unscoped.create(organization=org_a, registration_number="T 100 KEY")
    Vehicle.unscoped.create(organization=org_b, registration_number="X 200 OTH")
    resp = admin.post("/api/v1/api-keys/", {"name": "ERP sync", "scopes": ["vehicles.view"],
                                            "expires_in_days": 30}, format="json")
    assert resp.status_code == 201
    raw = resp.json()["key"]
    assert raw.startswith("gpk_") and not ApiKey.unscoped.filter(key_hash=raw).exists()   # only a hash is stored
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Api-Key {raw}")
    regs = [v["registration_number"] for v in client.get("/api/v1/vehicles/").json()["results"]]
    assert regs == ["T 100 KEY"]                                     # its own organization only
    assert client.get("/api/v1/drivers/").status_code == 403         # outside the key's scopes
    assert client.post("/api/v1/vehicles/", {"registration_number": "T 1 NEW"}, format="json").status_code == 403
    assert client.get("/api/v1/notifications/").status_code == 403   # personal routes are not for keys
    assert client.get("/api/v1/auth/me/").status_code == 401         # account routes never accept keys
    assert client.get("/api/v1/vehicles/", HTTP_X_ORGANIZATION_ID=str(org_b.pk)).status_code == 404
    header = APIClient()
    header.credentials(HTTP_X_API_KEY=raw)
    assert header.get("/api/v1/vehicles/").status_code == 200
    assert client.post("/api/v1/api-keys/", {"name": "x", "scopes": ["vehicles.view"]}).status_code == 403
    key = ApiKey.unscoped.get()
    assert key.last_used_at is not None
    admin.post(f"/api/v1/api-keys/{key.pk}/revoke/")
    assert client.get("/api/v1/vehicles/").status_code == 401
    assert {"api_keys.created", "api_keys.revoked"} <= set(AuditLog.objects.values_list("action", flat=True))


def test_api_key_cannot_exceed_its_creator(org_a, make_member, api):
    from apps.organizations.models import OrganizationMembership
    from apps.rbac.models import Role

    viewer_role = Role.objects.create(organization=org_a, slug="integrator", name="Integrator")
    viewer_role.permissions.set(viewer_role.permissions.model.objects.filter(
        codename__in=["integrations.manage", "vehicles.view"]))
    user = make_member(org_a, "viewer", "integrator@a.test")
    OrganizationMembership.unscoped.filter(user=user).update(role=viewer_role)
    client = api(user)
    bad = client.post("/api/v1/api-keys/", {"name": "x", "scopes": ["devices.manage"]}, format="json")
    assert bad.status_code == 400 and bad.json()["error"]["details"]["scopes"] == ["devices.manage"]
    raw = client.post("/api/v1/api-keys/", {"name": "x", "scopes": ["vehicles.view"]}, format="json").json()["key"]
    key_client = APIClient()
    key_client.credentials(HTTP_AUTHORIZATION=f"Api-Key {raw}")
    assert key_client.get("/api/v1/vehicles/").status_code == 200
    OrganizationMembership.unscoped.filter(user=user).update(is_active=False)   # creator removed: key stops
    assert key_client.get("/api/v1/vehicles/").status_code == 401


def test_bad_or_expired_keys_are_rejected(org_a, admin):
    raw = admin.post("/api/v1/api-keys/", {"name": "x", "scopes": ["vehicles.view"]}, format="json").json()["key"]
    ApiKey.unscoped.update(expires_at=timezone.now() - timedelta(minutes=1))
    for value in (raw, "gpk_nope", "not-a-key"):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Api-Key {value}")
        assert c.get("/api/v1/vehicles/").status_code == 401
    assert WebhookEvent.unscoped.count() == 0 and Webhook.unscoped.count() == 0


def test_forwarder_framework_is_prepared_only(org_a):
    from apps.integrations.forwarders import build_payload, check_mapping
    from apps.integrations.models import DataForwarder

    mapping = {"imei": "identifier", "lat": "latitude", "lng": "longitude"}
    assert check_mapping(mapping) == [] and check_mapping({"x": "driver_phone"}) == ["x: unknown source field "
                                                                                    "'driver_phone'"]
    assert build_payload({"identifier": "1", "latitude": -6.8, "longitude": 39.2, "driver": "secret"}, mapping) == {
        "imei": "1", "lat": -6.8, "lng": 39.2}
    f = DataForwarder.unscoped.create(organization=org_a, name="Regulator", url="https://example.com", mapping=mapping)
    assert f.is_active is False
