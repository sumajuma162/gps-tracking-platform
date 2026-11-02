from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "apps.core"

    def ready(self) -> None:
        from apps.core import signals  # noqa: F401
