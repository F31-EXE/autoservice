from django.contrib import admin
from django.db.models import Count, Sum

from .accounting import month_start
from .models import LLMBudgetAlert, LLMCall


@admin.register(LLMCall)
class LLMCallAdmin(admin.ModelAdmin):
    list_display = ("created_at", "station", "purpose", "model", "input_tokens", "output_tokens",
                    "cost_rub", "latency_ms", "success")  # fmt: skip
    list_filter = ("purpose", "success", "station")
    date_hierarchy = "created_at"
    readonly_fields = [f.name for f in LLMCall._meta.fields]

    def has_add_permission(self, request):
        return False

    def changelist_view(self, request, extra_context=None):
        rows = (
            LLMCall.objects.filter(created_at__date__gte=month_start())
            .values("station__name")
            .annotate(calls=Count("id"), cost=Sum("cost_rub"))
            .order_by("-cost")
        )
        extra_context = {**(extra_context or {}), "month_spend": rows}
        return super().changelist_view(request, extra_context)


@admin.register(LLMBudgetAlert)
class LLMBudgetAlertAdmin(admin.ModelAdmin):
    list_display = ("station", "month", "spent_rub", "created_at")
