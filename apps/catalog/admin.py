from django.contrib import admin

from .models import WorkType, WorkTypeAlias


class AliasInline(admin.TabularInline):
    model = WorkTypeAlias
    extra = 1
    fields = ("pattern", "kind", "priority", "station")


@admin.register(WorkType)
class WorkTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "category", "default_interval_km", "default_interval_months",
                    "urgency_default", "is_regular")  # fmt: skip
    list_filter = ("category", "is_regular", "urgency_default")
    search_fields = ("name", "code")
    inlines = [AliasInline]


@admin.register(WorkTypeAlias)
class WorkTypeAliasAdmin(admin.ModelAdmin):
    list_display = ("pattern", "work_type", "kind", "priority", "station")
    list_filter = ("kind", "station")
    search_fields = ("pattern",)
