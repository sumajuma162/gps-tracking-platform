from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from apps.accounts.models import User, UserSession


@admin.register(User)
class UserAdmin(BaseUserAdmin):  # type: ignore[type-arg]
    ordering = ["email"]
    list_display = ["email", "full_name", "is_active", "is_platform_admin", "locked_until", "last_login"]
    search_fields = ["email", "full_name"]
    fieldsets = (
        (None, {"fields": ("email", "password", "full_name", "language")}),
        ("Access", {"fields": ("is_active", "is_staff", "is_platform_admin", "is_superuser")}),
        ("Security", {"fields": ("failed_login_count", "locked_until", "password_changed_at", "last_login")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),)
    readonly_fields = ["last_login", "password_changed_at"]


@admin.register(UserSession)
class UserSessionAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ["user", "created_at", "last_used_at", "revoked_at", "ip_address"]
    readonly_fields = [f.name for f in UserSession._meta.fields]
