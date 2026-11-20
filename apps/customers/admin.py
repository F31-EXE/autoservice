from django.contrib import admin

from .models import Client, Vehicle


class VehicleInline(admin.TabularInline):
    model = Vehicle
    extra = 0
    fields = ("make", "model", "year", "last_visit_at", "last_known_mileage", "is_sold")
    readonly_fields = ("last_visit_at", "last_known_mileage")


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("id", "full_name", "phone_display", "station", "unsubscribed_at")
    list_filter = ("station",)
    search_fields = ("full_name", "external_id")
    inlines = [VehicleInline]

    @admin.display(description="телефон")
    def phone_display(self, obj: Client) -> str:
        request = getattr(self, "_request", None)
        if request is not None and request.user.is_superadmin:
            return obj.phone or ""
        return obj.masked_phone

    def get_queryset(self, request):
        self._request = request
        return super().get_queryset(request).for_user(request.user)

    def get_exclude(self, request, obj=None):
        return () if request.user.is_superadmin else ("phone",)


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("id", "make", "model", "year", "last_visit_at", "km_per_day_estimate")
    list_filter = ("client__station", "is_sold")
    raw_id_fields = ("client",)

    def get_queryset(self, request):
        return super().get_queryset(request).for_user(request.user)
