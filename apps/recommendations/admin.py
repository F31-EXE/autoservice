from django.contrib import admin

from .models import Recommendation


@admin.register(Recommendation)
class RecommendationAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "station", "source", "status", "due_date", "due_km",
                    "est_price", "confidence")  # fmt: skip
    list_filter = ("station", "status", "source", "urgency")
    raw_id_fields = ("vehicle", "source_work_order", "closed_by_work_order", "review_decision_by")
