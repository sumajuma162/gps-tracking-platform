from django.db import migrations


def forwards(apps, schema_editor):  # type: ignore[no-untyped-def]
    from apps.rbac.sync import sync_catalog

    sync_catalog(apps.get_model("rbac", "Permission"), apps.get_model("rbac", "Role"))


class Migration(migrations.Migration):
    dependencies = [("rbac", "0001_initial")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
