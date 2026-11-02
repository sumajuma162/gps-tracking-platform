from django.db import migrations

from common.rls import enable_tenant_rls


class Migration(migrations.Migration):
    dependencies = [("audit", "0002_initial"), ("core", "0001_runtime_role")]
    operations = [enable_tenant_rls("audit_logs")]
