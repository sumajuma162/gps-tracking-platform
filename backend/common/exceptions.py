"""Safe, consistent API error envelope: {"error": {...}, "request_id": "..."}."""
from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

log = logging.getLogger("api")


class ApiError(exceptions.APIException):
    def __init__(self, code: str, message: str, status_code: int = 400, details: Any = None) -> None:
        super().__init__(message, code)
        self.status_code = status_code
        self.error_code = code
        self.details = details


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    request = context.get("request")
    request_id = getattr(getattr(request, "_request", request), "request_id", None)
    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()
    response = exception_handler(exc, context)
    if response is None:
        log.exception("unhandled API error", extra={"request_id": request_id})
        return Response({"error": {"code": "server_error", "message": "Something went wrong. Try again; if it "
                                   "keeps failing, contact support with the request ID."},
                         "request_id": request_id}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    if isinstance(exc, ApiError):
        body: dict[str, Any] = {"code": exc.error_code, "message": str(exc.detail)}
        if exc.details is not None:
            body["details"] = exc.details
    elif isinstance(exc, exceptions.ValidationError):
        body = {"code": "validation_error", "message": "Some fields are invalid.", "details": response.data}
    else:
        detail = response.data.get("detail", "") if isinstance(response.data, dict) else response.data
        codes = exc.get_codes() if isinstance(exc, exceptions.APIException) else "error"
        body = {"code": codes if isinstance(codes, str) else "error", "message": str(detail)}
        if isinstance(exc, exceptions.Throttled) and exc.wait:
            body["retry_after_seconds"] = int(exc.wait)
    response.data = {"error": body, "request_id": request_id}
    return response
