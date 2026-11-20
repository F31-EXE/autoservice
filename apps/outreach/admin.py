from django.contrib import admin

from .models import Outreach, QueueRun


@admin.register(Outreach)
class OutreachAdmin(admin.ModelAdmin):
    list_display = ("id", "station", "planned_for", "status", "is_control_group", "channel",
                    "est_amount", "touch_count")  # fmt: skip
    list_filter = ("station", "status", "is_control_group", "channel")
    raw_id_fields = ("client", "vehicle", "recommendations", "approved_by", "attributed_work_order")
    date_hierarchy = "planned_for"


@admin.register(QueueRun)
class QueueRunAdmin(admin.ModelAdmin):
    list_display = ("station", "date", "stats")
    list_filter = ("station",)
