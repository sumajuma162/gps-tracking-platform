from __future__ import annotations

import uuid
from typing import Any

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager["User"]):
    use_in_migrations = True

    def create_user(self, email: str, password: str | None = None, **extra: Any) -> User:
        if not email:
            raise ValueError("email is required")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str | None = None, **extra: Any) -> User:
        extra.update(is_staff=True, is_superuser=True, is_platform_admin=True)
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    LANGUAGES = [("en", "English"), ("sw", "Kiswahili")]

    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=150, blank=True)
    language = models.CharField(max_length=5, choices=LANGUAGES, default="en")
    time_zone = models.CharField(max_length=40, default="Africa/Dar_es_Salaam")
    #: Non-critical notifications are held back between these local times (spec A11). Both empty = off.
    quiet_hours_start = models.TimeField(null=True, blank=True)
    quiet_hours_end = models.TimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False, help_text="Can open Django Admin (technical staff only).")
    is_platform_admin = models.BooleanField(default=False, help_text="Super Administrator of the whole platform.")
    date_joined = models.DateTimeField(default=timezone.now)
    failed_login_count = models.PositiveSmallIntegerField(default=0)
    first_failed_login_at = models.DateTimeField(null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    password_changed_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "users"
        ordering = ["email"]

    def __str__(self) -> str:
        return self.email

    def is_locked(self, now: Any = None) -> bool:
        return bool(self.locked_until and self.locked_until > (now or timezone.now()))


class UserSession(models.Model):
    """One login on one device; holds the current refresh-token id for rotation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sessions")
    refresh_jti = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=40, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)

    class Meta:
        db_table = "user_sessions"
        ordering = ["-last_used_at"]
        indexes = [models.Index(fields=["user", "revoked_at"])]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.pk}"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and self.expires_at > timezone.now()

    def revoke(self, reason: str) -> None:
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.revoked_reason = reason
            self.save(update_fields=["revoked_at", "revoked_reason"])
