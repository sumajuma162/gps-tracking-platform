import { hasPermission, useAuth } from "../store/auth";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import axios from "axios";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { LandingPage } from "../pages/LandingPage";
import { ForgotPasswordPage, LoginPage, ResetPasswordPage } from "../pages/AuthPages";
import { OverviewPage } from "../pages/OverviewPage";
import { UsersPage } from "../pages/UsersPage";
import { RolesPage } from "../pages/RolesPage";
import { AuditLogPage } from "../pages/AuditLogPage";
import { OrganizationsPage } from "../pages/OrganizationsPage";
import { ProfilePage } from "../pages/ProfilePage";
import { NotFoundPage } from "../pages/NotFoundPage";
import { AddDeviceWizard } from "../features/devices/AddDeviceWizard";
import { LiveMapPage } from "../features/live/LiveMapPage";
import { HistoryPage } from "../features/history/HistoryPage";
import { TripsPage } from "../features/analysis/TripsPage";
import { AlertsPage } from "../features/alerts/AlertsPage";
import { ReportsPage } from "../features/reports/ReportsPage";
import { SharePage } from "../features/sharing/SharePage";
import { SystemHealthPage } from "../features/system/SystemHealthPage";
import { AlertRulesPage } from "../features/alerts/AlertRulesPage";
import { GeofencesPage } from "../features/geofences/GeofencesPage";
import { IoProfileEditor } from "../features/platform/IoProfileEditor";
import { UnregisteredPage } from "../features/platform/UnregisteredPage";
import { ManualPositionPage } from "../features/platform/ManualPositionPage";
import { DeviceCatalogPage, DevicesPage, DriversPage, ListenerPortsPage, SimsPage, VehicleGroupsPage, VehiclesPage } from "../features/fleet/pages";
import { SettingsPage } from "../features/settings/SettingsPage";
import { IntegrationsPage } from "../features/integrations/IntegrationsPage";
import { MaintenancePage } from "../features/maintenance/MaintenancePage";
import { AnalyticsPage } from "../features/analytics/AnalyticsPage";
import { GenericFormatsPage } from "../features/formats/GenericFormatsPage";
import { MyVehiclePage } from "../features/driver/MyVehiclePage";
import { AppLayout } from "../layouts/AppLayout";
import { RequireAuth } from "../features/auth/RequireAuth";
import { RequirePermission } from "./RequirePermission";
import { Toaster } from "../components/Toast";

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        // Don't retry client errors (403/404); do retry flaky networks twice.
        retry: (count, err) => count < 2 && !(axios.isAxiosError(err) && err.response && err.response.status < 500),
      },
    },
  });
}

/** Drivers (portal only) land on their vehicle; everyone else on the overview. */
function HomePage() {
  const auth = useAuth();
  if (!hasPermission(auth, "dashboard.view") && hasPermission(auth, "own_vehicle.view")) {
    return <Navigate to="/app/my-vehicle" replace />;
  }
  return <OverviewPage />;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      <Route path="/share/:token" element={<SharePage />} />
      <Route path="/app" element={<RequireAuth><AppLayout /></RequireAuth>}>
        <Route index element={<HomePage />} />
        <Route path="users" element={<RequirePermission permission="users.view"><UsersPage /></RequirePermission>} />
        <Route path="roles" element={<RequirePermission permission="users.view"><RolesPage /></RequirePermission>} />
        <Route path="audit" element={<RequirePermission permission="audit.view"><AuditLogPage /></RequirePermission>} />
        <Route path="organizations" element={<RequirePermission platform><OrganizationsPage /></RequirePermission>} />
        <Route path="system" element={<SystemHealthPage />} />
        <Route path="reports" element={<RequirePermission permission="reports.view"><ReportsPage /></RequirePermission>} />
        <Route path="alerts" element={<RequirePermission permission="alerts.view"><AlertsPage /></RequirePermission>} />
        <Route path="alert-rules" element={<RequirePermission permission="alerts.manage"><AlertRulesPage /></RequirePermission>} />
        <Route path="trips" element={<RequirePermission permission="trips.view"><TripsPage /></RequirePermission>} />
        <Route path="geofences" element={<RequirePermission permission="geofences.view"><GeofencesPage /></RequirePermission>} />
        <Route path="history" element={<RequirePermission permission="routes.view_history"><HistoryPage /></RequirePermission>} />
        <Route path="live" element={<RequirePermission permission="tracking.view_live"><LiveMapPage /></RequirePermission>} />
        <Route path="vehicles" element={<RequirePermission permission="vehicles.view"><VehiclesPage /></RequirePermission>} />
        <Route path="vehicle-groups" element={<RequirePermission permission="vehicles.view"><VehicleGroupsPage /></RequirePermission>} />
        <Route path="devices" element={<RequirePermission permission="devices.view"><DevicesPage /></RequirePermission>} />
        <Route path="devices/new" element={<RequirePermission permission="devices.manage"><AddDeviceWizard /></RequirePermission>} />
        <Route path="unregistered" element={<RequirePermission platform><UnregisteredPage /></RequirePermission>} />
        <Route path="manual-position" element={<RequirePermission platformOrPermission="devices.manage"><ManualPositionPage /></RequirePermission>} />
        <Route path="device-catalog/io-profiles/:id" element={<RequirePermission platform><IoProfileEditor /></RequirePermission>} />
        <Route path="drivers" element={<RequirePermission permission="drivers.view"><DriversPage /></RequirePermission>} />
        <Route path="sims" element={<RequirePermission permission="sims.view"><SimsPage /></RequirePermission>} />
        <Route path="device-catalog/formats" element={<RequirePermission platform><GenericFormatsPage /></RequirePermission>} />
        <Route path="device-catalog" element={<RequirePermission platform><DeviceCatalogPage /></RequirePermission>} />
        <Route path="listener-ports" element={<RequirePermission platform><ListenerPortsPage /></RequirePermission>} />
        <Route path="my-vehicle" element={<RequirePermission permission="own_vehicle.view"><MyVehiclePage /></RequirePermission>} />
        <Route path="analytics" element={<RequirePermission permission="analytics.view"><AnalyticsPage /></RequirePermission>} />
        <Route path="maintenance" element={<RequirePermission permission="maintenance.view"><MaintenancePage /></RequirePermission>} />
        <Route path="integrations" element={<RequirePermission permission="integrations.manage"><IntegrationsPage /></RequirePermission>} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="profile" element={<ProfilePage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}

const queryClient = makeQueryClient();

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppRoutes />
        <Toaster />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
