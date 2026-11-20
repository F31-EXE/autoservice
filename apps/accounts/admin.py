from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Organization, ServiceStation, User


class StationInline(admin.TabularInline):
    model = ServiceStation
    extra = 0
    fields = ("name", "city", "timezone")


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "created_at")
    search_fields = ("name",)
    inlines = [StationInline]


@admin.register(ServiceStation)
class ServiceStationAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "city", "timezone")
    list_filter = ("organization",)
    search_fields = ("name", "city")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "organization", "role", "is_active")
    list_filter = ("role", "organization", "is_active")
    filter_horizontal = ("stations", "groups", "user_permissions")
    fieldsets = (
        *BaseUserAdmin.fieldsets,
        ("Vozvrat", {"fields": ("organization", "role", "stations")}),
    )
    add_fieldsets = (
        *BaseUserAdmin.add_fieldsets,
        ("Vozvrat", {"fields": ("organization", "role")}),
    )
