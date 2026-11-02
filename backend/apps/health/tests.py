from unittest import mock

import pytest


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200 and resp.json() == {"status": "ok"}


def test_health_rejects_post(client):
    assert client.post("/health").status_code == 405


@pytest.mark.django_db
def test_readiness_ready_when_dependencies_up(client):
    with mock.patch("apps.health.checks.check_redis", return_value=(True, "ok")):
        resp = client.get("/readiness")
    assert resp.status_code == 200
    assert resp.json()["checks"] == {"database": "ok", "migrations": "ok", "redis": "ok"}


@pytest.mark.django_db
def test_readiness_503_when_redis_down(client):
    with mock.patch("apps.health.checks.check_redis", return_value=(False, "ConnectionError")):
        resp = client.get("/readiness")
    assert resp.status_code == 503 and resp.json()["status"] == "not_ready"
