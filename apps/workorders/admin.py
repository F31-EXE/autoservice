from django.contrib import admin

from .models import ImportBatch, ImportMappingProfile, WorkOrder, WorkOrderLine


class LineInline(admin.TabularInline):
    model = WorkOrderLine
    extra = 0


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = ("external_number", "station", "opened_at", "total_amount", "status",
                    "extraction_status")  # fmt: skip
    list_filter = ("station", "status", "extraction_status")
    search_fields = ("external_number",)
    raw_id_fields = ("vehicle", "import_batch")
    inlines = [LineInline]


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "station", "original_filename", "status", "created_at")
    list_filter = ("station", "status", "source_system")


@admin.register(ImportMappingProfile)
class ImportMappingProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "station", "source_system", "layout")
