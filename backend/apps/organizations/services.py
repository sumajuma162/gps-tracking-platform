from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.accounts.models import User
from apps.audit.services import record
from apps.organizations.models import BreakGlassGrant, Organization, OrganizationMembership
from apps.rbac.models import Role
from common.exceptions import ApiError


def send_invitation(user: User, organization: Organization) -> None:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = f"{settings.FRONTEND_URL}/reset-password?uid={uid}&token={token}&invite=1"
    send_mail(f"You've been added to {organization.name}",
              f"You now have access to {organization.name} on the fleet tracking platform.\n\n"
              f"Set your password to sign in:\n{link}\n", settings.DEFAULT_FROM_EMAIL, [user.email])


def add_member(organization: Organization, email: str, full_name: str, role: Role, actor: Any,
               ip: str | None) -> OrganizationMembership:
    email = email.strip().lower()
    user = User.objects.filter(email=email).first()
    created = user is None
    if user is None:
        user = User.objects.create_user(email, full_name=full_name)
    if OrganizationMembership.unscoped.filter(organization=organization, user=user).exists():
        raise ApiError("already_member", "This person is already a member of the organization.", 400)
    membership = OrganizationMembership.unscoped.create(organization=organization, user=user, role=role)
    if created or not user.has_usable_password():
        send_invitation(user, organization)
    record("users.added", user=actor, organization=organization, resource=membership, ip=ip,
           metadata={"email": email, "role": role.slug, "new_account": created})
    return membership


def _other_active_admins(membership: OrganizationMembership) -> bool:
    return OrganizationMembership.unscoped.filter(
        organization_id=membership.organization_id, is_active=True, role__slug="org_admin",
        role__organization__isnull=True).exclude(pk=membership.pk).exists()


def change_role(membership: OrganizationMembership, role: Role, actor: Any, ip: str | None) -> None:
    old = membership.role
    if old.pk == role.pk:
        return
    if old.slug == "org_admin" and old.is_system and not _other_active_admins(membership):
        raise ApiError("last_admin", "This is the only active administrator. Add another administrator first.")
    membership.role = role
    membership.save(update_fields=["role", "updated_at"])
    record("users.role_changed", user=actor, organization=membership.organization, resource=membership, ip=ip,
           metadata={"email": membership.user.email, "from": old.slug, "to": role.slug})


def deactivate_member(membership: OrganizationMembership, actor: Any, ip: str | None) -> None:
    if membership.role.slug == "org_admin" and membership.role.is_system and not _other_active_admins(membership):
        raise ApiError("last_admin", "This is the only active administrator. Add another administrator first.")
    membership.is_active = False
    membership.save(update_fields=["is_active", "updated_at"])
    record("users.deactivated", user=actor, organization=membership.organization, resource=membership, ip=ip,
           metadata={"email": membership.user.email})


def grant_break_glass(user: User, organization: Organization, reason: str, ip: str | None) -> BreakGlassGrant:
    grant = BreakGlassGrant.objects.create(
        user=user, organization=organization, reason=reason,
        expires_at=timezone.now() + timedelta(hours=settings.BREAK_GLASS_HOURS))
    # Recorded against the organization so its administrators see it in their audit log.
    record("platform.break_glass_granted", user=user, organization=organization, resource=grant, ip=ip,
           metadata={"reason": reason, "expires_at": grant.expires_at.isoformat()})
    return grant
