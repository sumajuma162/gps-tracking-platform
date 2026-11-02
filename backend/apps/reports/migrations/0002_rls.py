from django.db import migrations

from common.rls import enable_tenant_rls


class Migration(migrations.Migration):
    dependencies = [("reports", "0001_initial"), ("core", "0001_runtime_role")]
    operations = [enable_tenant_rls("report_jobs"), enable_tenant_rls("scheduled_reports")]
