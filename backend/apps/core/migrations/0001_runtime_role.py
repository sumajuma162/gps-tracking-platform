from django.db import migrations

from common.rls import PostgresOnlySQL


class Migration(migrations.Migration):
    initial = True
    dependencies: list[tuple[str, str]] = []

    operations = [
        PostgresOnlySQL(
            sql=[
                """DO $$ BEGIN
                     IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gps_app') THEN
                       CREATE ROLE gps_app NOLOGIN NOSUPERUSER NOBYPASSRLS;
                     END IF;
                   END $$;""",
                "GRANT gps_app TO CURRENT_USER",
            ],
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
