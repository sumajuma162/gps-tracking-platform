from django.apps import AppConfig


class TrackingConfig(AppConfig):
    name = "apps.tracking"

    def ready(self) -> None:
        from common.rls import NO_ACCESS_TABLES, READ_ONLY_TABLES

        NO_ACCESS_TABLES.add("positions")         # compressed hypertable: no RLS possible, so no direct access
        READ_ONLY_TABLES.add("tenant_positions")  # tenant-filtered view the web app reads instead
