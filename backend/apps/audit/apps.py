from django.apps import AppConfig


class AuditConfig(AppConfig):
    name = "apps.audit"

    def ready(self) -> None:
        from common.rls import APPEND_ONLY_TABLES

        APPEND_ONLY_TABLES.add("audit_logs")
