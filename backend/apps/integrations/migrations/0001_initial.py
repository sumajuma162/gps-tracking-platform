import django.db.models.deletion
import django.db.models.manager
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

from common.rls import enable_tenant_rls

MANAGERS = [("objects", django.db.models.manager.Manager()), ("unscoped", django.db.models.manager.Manager())]
OPTS = {"abstract": False, "base_manager_name": "unscoped", "default_manager_name": "objects"}


def org():  # type: ignore[no-untyped-def]
    return models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+",
                             to="organizations.organization")


class Migration(migrations.Migration):
    initial = True
    dependencies = [("organizations", "0002_initial"), ("core", "0001_runtime_role"),
                    migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="Webhook",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("url", models.URLField(max_length=500)),
                ("events", models.JSONField(default=list, help_text="Subscribed event names.")),
                ("secret", models.CharField(max_length=80)),
                ("is_active", models.BooleanField(default=True)),
                ("consecutive_failures", models.PositiveIntegerField(default=0)),
                ("last_delivery_at", models.DateTimeField(blank=True, null=True)),
                ("last_status", models.CharField(blank=True, max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                 related_name="+", to=settings.AUTH_USER_MODEL)),
                ("organization", org()),
            ],
            options={"db_table": "webhooks", "ordering": ["name"], **OPTS},
            managers=MANAGERS,
        ),
        migrations.CreateModel(
            name="WebhookEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event", models.CharField(max_length=40)),
                ("dedup_key", models.CharField(max_length=200)),
                ("payload", models.JSONField(default=dict)),
                ("occurred_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("dispatched_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("organization", org()),
            ],
            options={"db_table": "webhook_events", "ordering": ["-created_at"], **OPTS},
            managers=MANAGERS,
        ),
        migrations.CreateModel(
            name="WebhookDelivery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "Pending"), ("succeeded", "Succeeded"),
                                                     ("failed", "Failed")], default="pending", max_length=10)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("next_attempt_at", models.DateTimeField(blank=True, null=True)),
                ("response_status", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("response_body", models.CharField(blank=True, max_length=500)),
                ("error", models.CharField(blank=True, max_length=300)),
                ("duration_ms", models.PositiveIntegerField(blank=True, null=True)),
                ("delivered_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("event", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="deliveries",
                                            to="integrations.webhookevent")),
                ("organization", org()),
                ("webhook", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="deliveries",
                                              to="integrations.webhook")),
            ],
            options={"db_table": "webhook_deliveries", "ordering": ["-created_at"], **OPTS},
            managers=MANAGERS,
        ),
        migrations.CreateModel(
            name="ApiKey",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("prefix", models.CharField(help_text="Shown in lists so a key can be recognised.", max_length=16)),
                ("key_hash", models.CharField(max_length=64, unique=True)),
                ("scopes", models.JSONField(default=list, help_text="Permission codenames this key may use.")),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                ("last_used_ip", models.GenericIPAddressField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="api_keys",
                                                 to=settings.AUTH_USER_MODEL)),
                ("organization", org()),
            ],
            options={"db_table": "api_keys", "ordering": ["-created_at"], **OPTS},
            managers=MANAGERS,
        ),
        migrations.AddConstraint(model_name="webhookevent", constraint=models.UniqueConstraint(
            fields=("organization", "dedup_key"), name="uniq_webhook_event")),
        migrations.AddIndex(model_name="webhookevent", index=models.Index(
            condition=models.Q(dispatched_at__isnull=True), fields=["dispatched_at", "created_at"],
            name="webhook_events_pending_idx")),
        migrations.AddIndex(model_name="webhookdelivery", index=models.Index(
            fields=["status", "next_attempt_at"], name="webhook_deliveries_due_idx")),
        migrations.AddConstraint(model_name="webhookdelivery", constraint=models.UniqueConstraint(
            fields=("webhook", "event"), name="uniq_webhook_delivery")),
        *[enable_tenant_rls(t) for t in ("webhooks", "webhook_events", "webhook_deliveries", "api_keys")],
    ]
