import django.db.models.deletion
import django.db.models.manager
from django.conf import settings
from django.db import migrations, models

from common.rls import enable_tenant_rls


def seed(apps, schema_editor):  # type: ignore[no-untyped-def]
    apps.get_model("settings_app", "PlatformSettings").objects.get_or_create(pk=1)


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ("organizations", "0002_initial"),
        ("core", "0001_runtime_role"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PlatformSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("values", models.JSONField(blank=True, default=dict, help_text="Overrides of the built-in defaults.")),
                ("map_tile_url", models.CharField(blank=True, help_text="Blank: MAP_TILE_URL from the environment.",
                                                  max_length=300)),
                ("map_attribution", models.CharField(blank=True, max_length=200)),
                ("geocoder_provider", models.CharField(choices=[("none", "None"),
                                                                ("nominatim", "Nominatim (OpenStreetMap)"),
                                                                ("custom", "Custom URL")],
                                                       default="none", max_length=20)),
                ("geocoder_url", models.URLField(blank=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                 related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "platform_settings"},
        ),
        migrations.CreateModel(
            name="OrganizationSettings",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("values", models.JSONField(blank=True, default=dict, help_text="This organization's overrides only.")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+",
                                                   to="organizations.organization")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                 related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "organization_settings", "abstract": False, "base_manager_name": "unscoped",
                     "default_manager_name": "objects"},
            managers=[("objects", django.db.models.manager.Manager()),
                      ("unscoped", django.db.models.manager.Manager())],
        ),
        migrations.AddConstraint(
            model_name="organizationsettings",
            constraint=models.UniqueConstraint(fields=("organization",), name="uniq_organization_settings"),
        ),
        migrations.RunPython(seed, migrations.RunPython.noop),
        enable_tenant_rls("organization_settings"),
    ]
