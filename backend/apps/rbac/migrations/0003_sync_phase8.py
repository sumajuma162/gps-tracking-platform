from django.db import migrations


def sync(apps, schema_editor):  # type: ignore[no-untyped-def]
    from apps.rbac.sync import sync_catalog

    sync_catalog(apps.get_model("rbac", "Permission"), apps.get_model("rbac", "Role"))


class Migration(migrations.Migration):
    dependencies = [("rbac", "0002_sync_catalog")]
    operations = [migrations.RunPython(sync, migrations.RunPython.noop)]
