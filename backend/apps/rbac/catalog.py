"""Permission catalog and system roles (spec A8). The database is synced from here."""
from __future__ import annotations

PERMISSIONS: dict[str, str] = {
    "dashboard.view": "View the dashboard",
    "tracking.view_live": "See vehicles on the live map",
    "routes.view_history": "View route history and playback",
    "vehicles.view": "View vehicles",
    "vehicles.manage": "Add, edit and deactivate vehicles",
    "devices.view": "View trackers",
    "devices.manage": "Register, edit and disable trackers",
    "drivers.view": "View drivers",
    "drivers.manage": "Add, edit and assign drivers",
    "sims.view": "View SIM cards",
    "sims.manage": "Manage SIM cards",
    "trips.view": "View trips and stops",
    "geofences.view": "View geofences",
    "geofences.manage": "Create and edit geofences",
    "alerts.manage": "Configure alert rules",
    "commands.send_output": "Send output-control commands such as immobilization (dangerous)",
    "alerts.view": "View alerts",
    "alerts.acknowledge": "Acknowledge and resolve alerts",
    "alert_rules.manage": "Configure alert rules",
    "notifications.view": "Receive notifications",
    "reports.view": "View reports",
    "reports.export": "Export reports",
    "analytics.view": "View analytics",
    "maintenance.view": "View maintenance",
    "maintenance.manage": "Manage maintenance schedules",
    "share_links.create": "Create live-location share links",
    "commands.send": "Send commands to trackers",
    "users.view": "View organization users",
    "users.manage": "Invite users and change roles",
    "roles.manage": "Create and edit custom roles",
    "organizations.manage": "Edit the organization profile",
    "settings.view": "View organization settings",
    "settings.manage": "Change organization settings",
    "integrations.manage": "Manage webhooks and API keys",
    "audit.view": "View the audit log",
    "own_vehicle.view": "See only the vehicle assigned to me",
}

#: Sorted codenames: one shared choice set for every API field that takes permissions (roles, API key scopes).
PERMISSION_CHOICES = sorted(PERMISSIONS)

_ALL = set(PERMISSIONS)
_VIEW_TRACKING = {"dashboard.view", "tracking.view_live", "routes.view_history", "vehicles.view", "devices.view",
                  "drivers.view", "trips.view", "geofences.view", "alerts.view", "notifications.view"}

SYSTEM_ROLES: dict[str, tuple[str, set[str]]] = {
    "org_admin": ("Organization administrator", _ALL - {"own_vehicle.view"}),
    "fleet_manager": ("Fleet manager", _VIEW_TRACKING | {
        "vehicles.manage", "drivers.manage", "sims.view", "alerts.acknowledge", "reports.view", "reports.export",
        "analytics.view", "maintenance.view", "maintenance.manage", "share_links.create", "users.view"}),
    "monitoring_officer": ("Monitoring officer", _VIEW_TRACKING | {"alerts.acknowledge"}),
    "viewer": ("Viewer", _VIEW_TRACKING | {"reports.view", "analytics.view"}),
    "auditor": ("Auditor", {"audit.view", "reports.view", "users.view", "dashboard.view"}),
    "driver": ("Driver", {"own_vehicle.view", "notifications.view"}),
}

#: Read-only access granted during Super Administrator break-glass sessions.
BREAK_GLASS_PERMISSIONS = {p for p in PERMISSIONS if p.endswith(".view") or p in (
    "routes.view_history", "tracking.view_live")} - {"own_vehicle.view"}

assert all(perms <= _ALL for _, perms in SYSTEM_ROLES.values())
