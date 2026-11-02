from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.alerts.views import (
    AlertRuleViewSet,
    AlertViewSet,
    ChannelViewSet,
    NotificationViewSet,
    PreferencesViewSet,
)
from apps.analysis.views import (
    DrivingEventViewSet,
    GeofenceEventViewSet,
    IdleEventViewSet,
    SpeedEventViewSet,
    StopViewSet,
    TripViewSet,
)
from apps.analytics.views import AnalyticsViewSet
from apps.audit.views import AuditLogViewSet, PlatformAuditLogViewSet
from apps.commands.views import CommandViewSet
from apps.core.insight import SearchViewSet, SystemHealthView
from apps.device_catalog.views import (
    CommandTemplateViewSet,
    DeviceModelViewSet,
    GenericFormatViewSet,
    IoProfileViewSet,
    ListenerPortViewSet,
    ManufacturerViewSet,
    ProtocolViewSet,
)
from apps.devices.inbox import UnregisteredDeviceViewSet
from apps.devices.views import DeviceViewSet
from apps.fleet.views import DriverViewSet, SimCardViewSet, VehicleGroupViewSet, VehicleViewSet
from apps.geofences.views import GeofenceViewSet
from apps.integrations.views import ApiKeyViewSet, WebhookViewSet
from apps.maintenance.views import ScheduleViewSet, ServiceRecordViewSet
from apps.organizations.views import MembershipViewSet, OrganizationViewSet
from apps.rbac.views import PermissionCatalogView, RoleViewSet
from apps.reports.views import ReportJobViewSet, ReportViewSet, ScheduledReportViewSet
from apps.settings_app.views import OrganizationSettingsViewSet, PlatformSettingsViewSet
from apps.sharing.views import PublicShareView, ShareLinkViewSet
from apps.tracking.driver_portal import MyVehicleViewSet
from apps.tracking.history_views import HistoryViewSet
from apps.tracking.live import DashboardViewSet, LiveViewSet, MapSettingsView
from apps.tracking.manual import ManualPositionView

router = DefaultRouter()
router.register("organizations", OrganizationViewSet, basename="organizations")
router.register("users", MembershipViewSet, basename="users")
router.register("roles", RoleViewSet, basename="roles")
router.register("audit-logs", AuditLogViewSet, basename="audit-logs")
router.register("platform/audit-logs", PlatformAuditLogViewSet, basename="platform-audit-logs")
router.register("device-catalog/manufacturers", ManufacturerViewSet, basename="catalog-manufacturers")
router.register("device-catalog/protocols", ProtocolViewSet, basename="catalog-protocols")
router.register("device-catalog/models", DeviceModelViewSet, basename="catalog-models")
router.register("device-catalog/command-templates", CommandTemplateViewSet, basename="catalog-command-templates")
router.register("device-catalog/io-profiles", IoProfileViewSet, basename="catalog-io-profiles")
router.register("device-catalog/listener-ports", ListenerPortViewSet, basename="catalog-listener-ports")
router.register("device-catalog/generic-formats", GenericFormatViewSet, basename="catalog-generic-formats")
router.register("sims", SimCardViewSet, basename="sims")
router.register("vehicle-groups", VehicleGroupViewSet, basename="vehicle-groups")
router.register("vehicles", VehicleViewSet, basename="vehicles")
router.register("drivers", DriverViewSet, basename="drivers")
router.register("devices", DeviceViewSet, basename="devices")
router.register("tracking/live", LiveViewSet, basename="tracking-live")
router.register("tracking/history", HistoryViewSet, basename="tracking-history")
router.register("trips", TripViewSet, basename="trips")
router.register("stops", StopViewSet, basename="stops")
router.register("idle-events", IdleEventViewSet, basename="idle-events")
router.register("speed-events", SpeedEventViewSet, basename="speed-events")
router.register("driving-events", DrivingEventViewSet, basename="driving-events")
router.register("geofences", GeofenceViewSet, basename="geofences")
router.register("geofence-events", GeofenceEventViewSet, basename="geofence-events")
router.register("alert-rules", AlertRuleViewSet, basename="alert-rules")
router.register("alerts", AlertViewSet, basename="alerts")
router.register("notifications", NotificationViewSet, basename="notifications")
router.register("notification-preferences", PreferencesViewSet, basename="notification-preferences")
router.register("notification-channels", ChannelViewSet, basename="notification-channels")
router.register("commands", CommandViewSet, basename="commands")
router.register("reports", ReportViewSet, basename="reports")
router.register("report-jobs", ReportJobViewSet, basename="report-jobs")
router.register("scheduled-reports", ScheduledReportViewSet, basename="scheduled-reports")
router.register("share-links", ShareLinkViewSet, basename="share-links")
router.register("search", SearchViewSet, basename="search")
router.register("webhooks", WebhookViewSet, basename="webhooks")
router.register("api-keys", ApiKeyViewSet, basename="api-keys")
router.register("maintenance/schedules", ScheduleViewSet, basename="maintenance-schedules")
router.register("maintenance/records", ServiceRecordViewSet, basename="maintenance-records")
router.register("analytics", AnalyticsViewSet, basename="analytics")
router.register("my-vehicle", MyVehicleViewSet, basename="my-vehicle")
router.register("dashboard/summary", DashboardViewSet, basename="dashboard-summary")
router.register("platform/unregistered-devices", UnregisteredDeviceViewSet, basename="platform-unregistered-devices")

urlpatterns = [
    path("auth/", include("apps.accounts.urls")),
    path("permissions/", PermissionCatalogView.as_view(), name="permissions"),
    path("settings/map/", MapSettingsView.as_view(), name="map-settings"),
    path("platform/manual-positions/", ManualPositionView.as_view(), name="platform-manual-positions"),
    path("tracking/manual-positions/", ManualPositionView.as_view(), name="tracking-manual-positions"),
    path("settings/organization/", OrganizationSettingsViewSet.as_view({"get": "list", "patch": "partial"}),
         name="settings-organization"),
    path("settings/platform/", PlatformSettingsViewSet.as_view({"get": "list", "patch": "partial"}),
         name="settings-platform"),
    path("public/share/<str:token>/", PublicShareView.as_view(), name="public-share"),
    path("system/health/", SystemHealthView.as_view(), name="system-health"),
    *router.urls,
]
