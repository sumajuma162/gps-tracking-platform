"""Liveness and readiness endpoints (spec A15). They reveal no internals."""
from __future__ import annotations

from django.db import transaction
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from apps.health import checks


@transaction.non_atomic_requests
@require_GET
def health(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


@transaction.non_atomic_requests
@require_GET
def readiness(request: HttpRequest) -> JsonResponse:
    results = {
        "database": checks.check_database(),
        "migrations": checks.check_migrations(),
        "redis": checks.check_redis(),
    }
    ready = all(ok for ok, _ in results.values())
    body = {"status": "ready" if ready else "not_ready",
            "checks": {name: detail for name, (_, detail) in results.items()}}
    return JsonResponse(body, status=200 if ready else 503)
