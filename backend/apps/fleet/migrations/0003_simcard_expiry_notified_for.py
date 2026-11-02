from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("fleet", "0002_vehicle_trip_detection_mode")]
    operations = [migrations.AddField(
        model_name="simcard", name="expiry_notified_for",
        field=models.DateField(blank=True, editable=False, null=True))]
