"""Notification delivery providers (spec A11). In-app is delivered by the processor over WebSockets; email is real
(SMTP through Django); SMS, WhatsApp and push have the interface and per-organization settings only, with no vendor
hardcoded. A provider raises ``DeliveryError`` to record a failed attempt."""
from __future__ import annotations

from typing import Protocol

from django.conf import settings
from django.core.mail import EmailMessage

from apps.alerts.models import Notification


class DeliveryError(Exception):
    def __init__(self, message: str, retry: bool = True) -> None:
        super().__init__(message)
        self.retry = retry


class NotificationProvider(Protocol):
    channel: str

    def send(self, notification: Notification) -> None: ...


class EmailProvider:
    channel = "email"

    def send(self, notification: Notification) -> None:
        if not notification.user.email:
            raise DeliveryError("user has no email address", retry=False)
        footer = f"\n\n{settings.FRONTEND_URL}/app/alerts" if notification.alert_id else ""
        try:
            EmailMessage(subject=f"[{notification.severity.upper()}] {notification.title}",
                         body=notification.body + footer, to=[notification.user.email]).send(fail_silently=False)
        except Exception as exc:  # SMTP refused, timed out, ...
            raise DeliveryError(f"{type(exc).__name__}: {exc}"[:300]) from exc


class NotConfiguredProvider:
    """SMS / WhatsApp / push: the interface exists; a vendor integration is added per deployment."""

    def __init__(self, channel: str) -> None:
        self.channel = channel

    def send(self, notification: Notification) -> None:
        raise DeliveryError(f"no {self.channel} provider is configured", retry=False)


def provider_for(channel: str) -> NotificationProvider:
    return EmailProvider() if channel == "email" else NotConfiguredProvider(channel)
