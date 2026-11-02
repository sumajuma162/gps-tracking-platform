from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "apps.accounts"

    def ready(self) -> None:
        from apps.accounts import schema  # noqa: F401  (registers the OpenAPI auth extension)
