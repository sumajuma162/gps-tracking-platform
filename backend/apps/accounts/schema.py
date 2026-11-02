"""OpenAPI description of the Bearer JWT authentication."""
from __future__ import annotations

from typing import Any

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class JWTAuthenticationScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    target_class = "apps.accounts.authentication.JWTAuthentication"
    name = "BearerAuth"

    def get_security_definition(self, auto_schema: Any) -> dict[str, Any]:
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT",
                "description": "Access token from POST /api/v1/auth/login/ (10-minute lifetime). Tenant endpoints "
                               "also take an X-Organization-Id header when the user belongs to several organizations."}


class ApiKeyAuthenticationScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    target_class = "apps.integrations.authentication.ApiKeyAuthentication"
    name = "ApiKeyAuth"

    def get_security_definition(self, auto_schema: Any) -> dict[str, Any]:
        return {"type": "apiKey", "in": "header", "name": "X-Api-Key",
                "description": "Organization API key (also accepted as `Authorization: Api-Key <key>`). Works on "
                               "organization endpoints only, limited to the key's scopes."}
