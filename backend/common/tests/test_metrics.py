"""Backend Prometheus metrics (spec A15)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def test_metrics_endpoint_counts_routes_and_reads_platform_gauges(client, settings, org_a, make_member, api):
    api(make_member(org_a, "viewer")).get("/api/v1/vehicles/")
    body = client.get("/metrics").content.decode()
    assert 'gps_http_requests_total{method="GET",route="api/v1/vehicles/",status="200"}' in body
    assert "gps_http_request_duration_seconds_bucket" in body and "gps_websocket_clients" in body
    assert 'gps_queue_items{queue="notifications"}' in body
    settings.METRICS_TOKEN = "scrape-secret"
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", HTTP_AUTHORIZATION="Bearer scrape-secret").status_code == 200
