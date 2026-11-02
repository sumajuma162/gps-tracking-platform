"""API key authentication for organization API routes (spec A13 "API keys").

``Authorization: Api-Key <key>`` or ``X-Api-Key: <key>``. A key works only on tenant routes (``TenantViewSet``),
only in its own organization, and only with permissions that are both in its scopes and still held by its
creator in that organization. Account, platform and WebSocket-ticket endpoints never accept keys.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.utils import timezone
from rest_framework import authentication, exceptions
from rest_framework.request import Request

from apps.integrations.models import ApiKey, key_hash
from common.middleware import client_ip
from common.rls import rls_bypass

KEY_PREFIX = "gpk_"


class ApiKeyAuthentication(authentication.BaseAuthentication):
    keyword = "Api-Key"

    def authenticate(self, request: Request) -> tuple[Any, ApiKey] | None:
        raw = request.headers.get("X-Api-Key", "")
        header = authentication.get_authorization_header(request).split()
        if not raw and header and header[0].lower() == self.keyword.lower().encode():
            if len(header) != 2:
                raise exceptions.AuthenticationFailed("Invalid API key header.")
            raw = header[1].decode(errors="replace")
        if not raw:
            return None
        if not raw.startswith(KEY_PREFIX):
            raise exceptions.AuthenticationFailed("Invalid API key.", code="api_key_invalid")
        with rls_bypass():                      # looked up by the secret only; the tenant is the key's own
            key = ApiKey.unscoped.select_related("created_by", "organization").filter(key_hash=key_hash(raw)).first()
            if key is None or not key.is_active or not key.created_by.is_active:
                raise exceptions.AuthenticationFailed("This API key is invalid, expired or revoked.",
                                                      code="api_key_invalid")
            now = timezone.now()
            if key.last_used_at is None or now - key.last_used_at > timedelta(minutes=1):
                ApiKey.unscoped.filter(pk=key.pk).update(last_used_at=now, last_used_ip=client_ip(request))
        return key.created_by, key

    def authenticate_header(self, request: Request) -> str:
        return self.keyword
