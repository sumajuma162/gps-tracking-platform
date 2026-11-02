import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MockAdapter from "axios-mock-adapter";
import { api } from "../../services/api";
import { AppRoutes } from "../../app/App";
import { setLanguage } from "../../i18n";
import { renderAt, signIn, signOut } from "../../test-utils";
import { visibleNav } from "../navigation/nav";
import { membership } from "../../test-utils";

let mock: MockAdapter;
beforeEach(() => { mock = new MockAdapter(api); setLanguage("en"); });
afterEach(() => { mock.restore(); signOut(); });

const schema = [
  { key: "offline_minutes", group: "status", min: 1, max: 1440, choices: [], integer: true },
  { key: "overspeed_warning_kmh", group: "speed", min: 10, max: 250, choices: [], integer: false },
  { key: "email_min_severity", group: "notifications", min: null, max: null, choices: ["info", "warning", "critical"], integer: false },
];
const orgBody = { organization: { name: "Demo", timezone: "Africa/Dar_es_Salaam", language: "en", distance_unit: "km" },
  values: { offline_minutes: 5, overspeed_warning_kmh: 70, email_min_severity: "critical" },
  overrides: { overspeed_warning_kmh: 70 }, defaults: { offline_minutes: 5, overspeed_warning_kmh: 80, email_min_severity: "critical" }, schema };

describe("navigation", () => {
  it("lists the part B modules by permission", () => {
    const keys = visibleNav({ isPlatformAdmin: false, memberships: [membership(["analytics.view", "maintenance.view", "settings.view"])], activeOrgId: 1 }).map((i) => i.key);
    expect(keys).toEqual(expect.arrayContaining(["analytics", "maintenance", "settings"]));
    expect(keys).not.toContain("integrations");
  });
});

describe("settings", () => {
  it("shows inherited defaults and saves only what changed, with null to reset", async () => {
    signIn(["settings.view", "settings.manage"]);
    mock.onGet("/settings/organization/").reply(200, orgBody);
    mock.onPatch("/settings/organization/").reply(200, orgBody);
    renderAt(<AppRoutes />, "/app/settings");
    const offline = await screen.findByLabelText("Offline after (min)");
    expect(screen.getByText("Default: 80")).toBeInTheDocument();
    await userEvent.type(offline, "15");
    const speed = screen.getByLabelText("Warning speed (km/h)");
    await userEvent.click(within(speed.parentElement as HTMLElement).getByRole("button", { name: "Use default" }));
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(mock.history.patch.length).toBe(1));
    expect(JSON.parse(mock.history.patch[0].data as string)).toEqual({ values: { offline_minutes: 15, overspeed_warning_kmh: null } });
  });

  it("is read-only without settings.manage", async () => {
    signIn(["settings.view"]);
    mock.onGet("/settings/organization/").reply(200, orgBody);
    renderAt(<AppRoutes />, "/app/settings");
    expect(await screen.findByLabelText("Offline after (min)")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save changes" })).not.toBeInTheDocument();
  });
});

describe("integrations", () => {
  it("shows a new webhook's signing secret once", async () => {
    signIn(["integrations.manage", "vehicles.view"]);
    mock.onGet("/webhooks/").reply(200, { count: 0, next: null, previous: null, results: [] });
    mock.onPost("/webhooks/").reply(201, { id: 1, secret: "whsec_abc" });
    renderAt(<AppRoutes />, "/app/integrations");
    await userEvent.click(await screen.findByRole("button", { name: "Add webhook" }));
    await userEvent.type(screen.getByLabelText("Name"), "ERP");
    await userEvent.type(screen.getByLabelText("Endpoint URL (https)"), "https://erp.example.com/hook");
    await userEvent.click(screen.getByLabelText("Trip completed"));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Add webhook" }));
    expect(await screen.findByTestId("one-time-secret")).toHaveTextContent("whsec_abc");
    expect(JSON.parse(mock.history.post[0].data as string)).toEqual({ name: "ERP", url: "https://erp.example.com/hook", events: ["alert.created", "trip.completed"] });
  });

  it("creates an API key limited to chosen permissions", async () => {
    signIn(["integrations.manage", "vehicles.view", "trips.view"]);
    mock.onGet("/webhooks/").reply(200, { count: 0, next: null, previous: null, results: [] });
    mock.onGet("/api-keys/").reply(200, { count: 0, next: null, previous: null, results: [] });
    mock.onPost("/api-keys/").reply(201, { id: 1, key: "gpk_1234_secret" });
    renderAt(<AppRoutes />, "/app/integrations");
    await userEvent.click(await screen.findByRole("tab", { name: "API keys" }));
    await userEvent.click(await screen.findByRole("button", { name: "Create API key" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByLabelText("integrations.manage")).not.toBeInTheDocument();   // never delegable
    await userEvent.type(within(dialog).getByLabelText("Name"), "Sync");
    await userEvent.click(within(dialog).getByLabelText("vehicles.view"));
    await userEvent.click(within(dialog).getByRole("button", { name: "Create API key" }));
    expect(await screen.findByTestId("one-time-secret")).toHaveTextContent("gpk_1234_secret");
    expect(JSON.parse(mock.history.post[0].data as string)).toEqual({ name: "Sync", scopes: ["vehicles.view"], expires_in_days: null });
  });
});

describe("maintenance, analytics and audit", () => {
  it("shows the due state of schedules", async () => {
    signIn(["maintenance.view"]);
    mock.onGet("/maintenance/schedules/").reply(200, { count: 1, next: null, previous: null, results: [
      { id: 1, vehicle: 1, vehicle_registration: "T 123 ABC", name: "Oil change", interval_km: 5000, interval_engine_hours: null,
        interval_days: null, last_service_at: "2026-09-01T08:00:00Z", status: { state: "overdue", km_left: -120, hours_left: null, days_left: null } }] });
    renderAt(<AppRoutes />, "/app/maintenance");
    expect((await screen.findAllByText("Overdue")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("120 km over").length).toBeGreaterThan(0);
  });

  it("renders analytics charts for the chosen period", async () => {
    signIn(["analytics.view"]);
    mock.onGet("/analytics/").reply(200, { vehicles: 1, status_distribution: { ONLINE_MOVING: 2 }, days: [
      { day: "2026-10-02", distance_km: 40, trips: 2, driving_h: 1, idle_h: 0, utilization_pct: 4, overspeed: 1, overspeed_critical: 0,
        idle_events: 0, idle_minutes: 0, alerts_info: 0, alerts_warning: 1, alerts_critical: 0, records: 600, late_records: 0, uptime_pct: 50 }] });
    renderAt(<AppRoutes />, "/app/analytics");
    expect(await screen.findByRole("region", { name: "Distance per day (km)" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Tracker uptime (%)" })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Period"), "30");
    await waitFor(() => expect(mock.history.get.filter((r) => r.url === "/analytics/").length).toBe(2));
  });

  it("sends audit search filters", async () => {
    signIn(["audit.view"]);
    mock.onGet("/audit-logs/facets/").reply(200, { actions: ["vehicles.updated"], resource_types: ["fleet.vehicle"] });
    mock.onGet("/audit-logs/").reply(200, { count: 0, next: null, previous: null, results: [] });
    renderAt(<AppRoutes />, "/app/audit");
    await userEvent.type(await screen.findByPlaceholderText("Search text"), "port");
    await userEvent.selectOptions(screen.getByLabelText("Resource"), "fleet.vehicle");
    await waitFor(() => {
      const last = mock.history.get.filter((r) => r.url === "/audit-logs/").at(-1);
      expect(last?.params).toMatchObject({ q: "port", resource_type: "fleet.vehicle" });
    });
  });
});

describe("driver portal", () => {
  it("lands drivers on their vehicle and shows only it", async () => {
    signIn(["own_vehicle.view", "notifications.view"]);
    mock.onGet("/my-vehicle/").reply(200, { vehicle: { registration_number: "T 123 ABC", make: "Isuzu", model: "FVR" }, driver: "Hamisi Juma",
      live: { status: "ONLINE_MOVING", latitude: -6.8, longitude: 39.2, speed_kmh: 42, ignition: true, device_time: "2026-10-04T08:00:00Z" },
      trips: [{ id: 1, start_time: "2026-10-04T06:00:00Z", end_time: "2026-10-04T06:30:00Z", distance_km: 12.5, duration_s: 1800, max_speed_kmh: 70 }] });
    renderAt(<AppRoutes />, "/app");
    expect(await screen.findByText("T 123 ABC")).toBeInTheDocument();
    expect(screen.getByText("42 km/h")).toBeInTheDocument();
    expect(screen.getByText("12.5")).toBeInTheDocument();
    const keys = visibleNav({ isPlatformAdmin: false, memberships: [membership(["own_vehicle.view", "notifications.view"])], activeOrgId: 1 }).map((i) => i.key);
    expect(keys).toContain("myVehicle");
    expect(keys).not.toContain("liveMap");
  });
});
