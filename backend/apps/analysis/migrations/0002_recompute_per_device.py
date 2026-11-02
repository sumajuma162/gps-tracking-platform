import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("analysis", "0001_initial")]
    operations = [
        migrations.AddField(model_name="recomputerequest", name="first_late",
                            field=models.DateTimeField(default=django.utils.timezone.now,
                                                       help_text="Earliest late record: results from here on are "
                                                                 "marked delayed."),
                            preserve_default=False),
        migrations.RemoveConstraint(model_name="recomputerequest", name="one_pending_recompute_per_window"),
        migrations.AddConstraint(model_name="recomputerequest",
                                 constraint=models.UniqueConstraint(condition=models.Q(done_at__isnull=True),
                                                                    fields=("device",),
                                                                    name="one_pending_recompute_per_device")),
    ]
