import type { LucideIcon } from "lucide-react";
import { Building2, Cable, Car, Cpu, FileClock, LayoutDashboard, Radio, ShieldCheck, Smartphone, UserRound, Users, Contact, Inbox, MapPinned, Route, Hexagon, Flag, Siren, FileBarChart, Activity, BarChart3, Wrench, Webhook, Settings, Braces, Truck } from "lucide-react";
import { activeMembership, type Membership } from "../../store/auth";

export interface NavItem {
  key: string;            // i18n key under "nav"
  to: string;
  icon: LucideIcon;
  /** Permission needed in the active organization; null = any member. */
  permission?: string | null;
  platformOnly?: boolean;
  platformAdminOrPermission?: string;
}

/**
 * Menu entries for modules that exist. Later phases append items here (live map, vehicles, ...);
 * nothing is listed before its page works.
 */
export const NAV_ITEMS: NavItem[] = [
  { key: "overview", to: "/app", icon: LayoutDashboard, permission: null },
  { key: "liveMap", to: "/app/live", icon: MapPinned, permission: "tracking.view_live" },
  { key: "alerts", to: "/app/alerts", icon: Siren, permission: "alerts.view" },
  { key: "trips", to: "/app/trips", icon: Flag, permission: "trips.view" },
  { key: "history", to: "/app/history", icon: Route, permission: "routes.view_history" },
  { key: "geofences", to: "/app/geofences", icon: Hexagon, permission: "geofences.view" },
  { key: "reports", to: "/app/reports", icon: FileBarChart, permission: "reports.view" },
  { key: "myVehicle", to: "/app/my-vehicle", icon: Truck, permission: "own_vehicle.view" },
  { key: "analytics", to: "/app/analytics", icon: BarChart3, permission: "analytics.view" },
  { key: "maintenance", to: "/app/maintenance", icon: Wrench, permission: "maintenance.view" },
  { key: "vehicles", to: "/app/vehicles", icon: Car, permission: "vehicles.view" },
  { key: "devices", to: "/app/devices", icon: Radio, permission: "devices.view" },
  { key: "drivers", to: "/app/drivers", icon: Contact, permission: "drivers.view" },
  { key: "sims", to: "/app/sims", icon: Smartphone, permission: "sims.view" },
  { key: "users", to: "/app/users", icon: Users, permission: "users.view" },
  { key: "roles", to: "/app/roles", icon: ShieldCheck, permission: "users.view" },
  { key: "audit", to: "/app/audit", icon: FileClock, permission: "audit.view" },
  { key: "integrations", to: "/app/integrations", icon: Webhook, permission: "integrations.manage" },
  { key: "settings", to: "/app/settings", icon: Settings, permission: "settings.view" },
  { key: "organizations", to: "/app/organizations", icon: Building2, platformOnly: true },
  { key: "catalog", to: "/app/device-catalog", icon: Cpu, platformOnly: true },
  { key: "system", to: "/app/system", icon: Activity, platformOnly: true },
  { key: "ports", to: "/app/listener-ports", icon: Cable, platformOnly: true },
  { key: "formats", to: "/app/device-catalog/formats", icon: Braces, platformOnly: true },
  { key: "unregistered", to: "/app/unregistered", icon: Inbox, platformOnly: true },
  { key: "manualPosition", to: "/app/manual-position", icon: MapPinned, platformAdminOrPermission: "devices.manage" },
  { key: "profile", to: "/app/profile", icon: UserRound, permission: null },
];

export function visibleNav(opts: { isPlatformAdmin: boolean; memberships: Membership[]; activeOrgId: number | null }): NavItem[] {
  const membership = activeMembership(opts);
  return NAV_ITEMS.filter((item) => {
    if (item.platformOnly) return opts.isPlatformAdmin;
    if (item.platformAdminOrPermission) {
      return opts.isPlatformAdmin || membership?.permissions.includes(item.platformAdminOrPermission) === true;
    }
    if (item.key === "settings" && opts.isPlatformAdmin) return true;      // platform defaults live on the same page
    if (item.permission === null || item.permission === undefined) return membership !== null || item.key === "profile";
    return membership?.permissions.includes(item.permission) ?? false;
  });
}
