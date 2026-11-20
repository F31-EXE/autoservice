from django.contrib import admin

from .models import BookingRequest, MaxLink, Message


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "channel", "direction", "status", "touch_number", "intent",
                    "needs_attention")  # fmt: skip
    list_filter = ("channel", "direction", "status", "intent", "needs_attention")
    raw_id_fields = ("client", "outreach", "handled_by", "sent_by")


@admin.register(BookingRequest)
class BookingRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "status", "preferred_time_text")
    list_filter = ("status",)
    raw_id_fields = ("client", "outreach", "assigned_to")


@admin.register(MaxLink)
class MaxLinkAdmin(admin.ModelAdmin):
    list_display = ("id", "client", "used_at", "created_at")
    raw_id_fields = ("client",)
