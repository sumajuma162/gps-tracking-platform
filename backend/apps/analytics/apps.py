from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    name = "apps.analytics"
    label = "analytics"

    def ready(self) -> None:
        from common.rls import NO_ACCESS_TABLES, READ_ONLY_TABLES

        NO_ACCESS_TABLES.add("positions_hourly")          # continuous aggregate: no RLS; read through the view
        READ_ONLY_TABLES.add("tenant_positions_hourly")
