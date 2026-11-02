from __future__ import annotations

from celery import shared_task

from apps.integrations import dispatch


@shared_task(name="integrations.dispatch_webhooks")
def dispatch_webhooks() -> dict[str, int]:
    """Every few seconds: fan new events out to webhooks, then send what is due (first tries and retries)."""
    created = dispatch.fan_out()
    return {"created": created, **dispatch.deliver_due()}
