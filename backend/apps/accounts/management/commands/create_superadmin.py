"""Create the first platform Super Administrator (production-safe; prompts for the password)."""
from __future__ import annotations

import getpass
import os
from typing import Any

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User


class Command(BaseCommand):
    help = "Create a platform Super Administrator. Password from SUPERADMIN_PASSWORD or an interactive prompt."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("email")
        parser.add_argument("--name", default="")

    def handle(self, *args: Any, email: str, name: str, **options: Any) -> None:
        email = email.strip().lower()
        if User.objects.filter(email=email).exists():
            raise CommandError(f"A user with email {email} already exists.")
        password = os.environ.get("SUPERADMIN_PASSWORD") or getpass.getpass("Password: ")
        user = User(email=email, full_name=name)
        try:
            validate_password(password, user)
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc
        User.objects.create_superuser(email, password, full_name=name)
        self.stdout.write(self.style.SUCCESS(f"Super Administrator {email} created."))
