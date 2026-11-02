"""Generic formats are parsed only by the gateway (spec A18: protocols are never parsed in Django views).

The web app asks the gateway's internal endpoint ``POST /test-parse`` to validate a definition and to run the UI's
test-parse box. ``GATEWAY_INTERNAL_URL`` is reachable only on the internal Docker network.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from django.conf import settings

from common.exceptions import ApiError


def gateway_parse(definition: dict[str, Any], sample: str) -> dict[str, Any]:
    body = json.dumps({"definition": definition, "sample": sample}).encode()
    req = urllib.request.Request(f"{settings.GATEWAY_INTERNAL_URL}/test-parse", data=body, method="POST",  # noqa: S310
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - fixed internal URL from settings
            return json.loads(resp.read())  # type: ignore[no-any-return]
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise ApiError("gateway_unavailable", "The gateway can't be reached to check this format. Try again.",
                       503) from exc


def check_definition(kind: str, definition: dict[str, Any]) -> dict[str, Any]:
    """Raise a field-level validation error when the gateway rejects the definition."""
    if not isinstance(definition, dict):
        raise ApiError("invalid_format", "The definition must be an object.", 400, {"definition": ["not an object"]})
    definition = {**definition, "kind": kind}
    result = gateway_parse(definition, "")
    if result.get("stage") == "format" and not result.get("ok"):
        raise ApiError("invalid_format", "The format definition is not valid.", 400,
                       {"definition": [f"{e['field']}: {e['message']}" for e in result.get("errors", [])]})
    return definition
