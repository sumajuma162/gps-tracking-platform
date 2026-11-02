import django.db.models.deletion
import django.db.models.manager
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
    dependencies = [("organizations", "0002_initial"), ("fleet", "0002_vehicle_trip_detection_mode"),
                    ("core", "0001_runtime_role"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="MaintenanceSchedule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(help_text="e.g. Oil change", max_length=100)),
                ("interval_km", models.PositiveIntegerField(blank=True, null=True)),
                ("interval_engine_hours", models.PositiveIntegerField(blank=True, null=True)),
                ("interval_days", models.PositiveIntegerField(blank=True, null=True)),
                ("due_soon_km", models.PositiveIntegerField(default=500)),
                ("due_soon_engine_hours", models.PositiveIntegerField(default=10)),
                ("due_soon_days", models.PositiveIntegerField(default=7)),
                ("last_service_at", models.DateTimeField(help_text="Counting starts here.")),
                ("is_active", models.BooleanField(default=True)),
                ("notes", models.TextField(blank=True)),
                ("alert_state", models.CharField(choices=[("ok", "OK"), ("due_soon", "Due soon"),
                                                          ("overdue", "Overdue")], default="ok", max_length=10)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("organization", org()),
                ("vehicle", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                              related_name="maintenance_schedules", to="fleet.vehicle")),
            ],
            options={"db_table": "maintenance_schedules", "ordering": ["vehicle__registration_number", "name"],
                     **OPTS},
            managers=MANAGERS,
        ),
        migrations.CreateModel(
            name="ServiceRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("performed_at", models.DateTimeField()),
                ("title", models.CharField(max_length=150)),
                ("odometer_km", models.DecimalField(blank=True, decimal_places=1, max_digits=10, null=True)),
                ("engine_hours", models.DecimalField(blank=True, decimal_places=1, max_digits=9, null=True)),
                ("cost", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("currency", models.CharField(default="TZS", max_length=3)),
                ("vendor", models.CharField(blank=True, max_length=120)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                 related_name="+", to=settings.AUTH_USER_MODEL)),
                ("organization", org()),
                ("schedule", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                               related_name="records", to="maintenance.maintenanceschedule")),
                ("vehicle", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                              related_name="service_records", to="fleet.vehicle")),
            ],
            options={"db_table": "service_records", "ordering": ["-performed_at"], **OPTS},
            managers=MANAGERS,
        ),
        migrations.AddConstraint(model_name="maintenanceschedule", constraint=models.CheckConstraint(
            condition=models.Q(("interval_km__isnull", False), ("interval_engine_hours__isnull", False),
                               ("interval_days__isnull", False), _connector="OR"),
            name="maintenance_schedule_has_interval")),
        enable_tenant_rls("maintenance_schedules"),
        enable_tenant_rls("service_records"),
    ]
