from django.apps import AppConfig


class OutreachConfig(AppConfig):
    name = "apps.outreach"
    label = "outreach"
    verbose_name = "Напоминания"

    def ready(self) -> None:
        from . import receivers  # noqa: F401
