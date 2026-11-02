from django.db import migrations

from common.rls import enable_tenant_rls

TABLES = ["alert_rules", "alerts", "notifications", "notification_preferences", "notification_channels", "commands"]


class Migration(migrations.Migration):
    dependencies = [("alerts", "0001_initial"), ("commands", "0001_initial"), ("core", "0001_runtime_role")]
    operations = [enable_tenant_rls(t) for t in TABLES]
