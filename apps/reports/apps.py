from django.apps import AppConfig


class ReportsConfig(AppConfig):
    name = "apps.reports"
    label = "reports"
    verbose_name = "Отчёты"

    def ready(self) -> None:
        from . import receivers  # noqa: F401
