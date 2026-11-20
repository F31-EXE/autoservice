from django.apps import AppConfig


class RecommendationsConfig(AppConfig):
    name = "apps.recommendations"
    label = "recommendations"
    verbose_name = "Рекомендации"

    def ready(self) -> None:
        from . import receivers  # noqa: F401
