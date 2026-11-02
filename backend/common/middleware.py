"""Request ID middleware: accepts X-Request-ID or generates one, echoes it back."""
from __future__ import annotations

import re
import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class RequestIdMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming = request.headers.get("X-Request-ID", "")
        request.request_id = incoming if _SAFE_ID.match(incoming) else uuid.uuid4().hex  # type: ignore[attr-defined]
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id  # type: ignore[attr-defined]
        return response


def client_ip(request: HttpRequest) -> str | None:
    """Nginx sets X-Real-IP; never trust a client-supplied value beyond the proxy."""
    return request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR")
