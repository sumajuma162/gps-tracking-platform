import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MockAdapter from "axios-mock-adapter";
import { api } from "../../services/api";
import { AppRoutes } from "../../app/App";
import { startTracking } from "../../services/tracking";
import { useLive, type LiveVehicle } from "../../store/live";
import { setLanguage } from "../../i18n";
import { renderAt, signIn, signOut } from "../../test-utils";
import type { MarkerSpec } from "../../maps/types";

const markers: { current: MarkerSpec[]; panned: unknown[] } = { current: [], panned: [] };
vi.mock("../../maps", () => ({
  createMap: () => ({
    setMarkers: (m: MarkerSpec[]) => { markers.current = m; }, fitTo: () => undefined,
    panTo: (p: unknown) => { markers.panned.push(p); }, onMarkerClick: () => undefined, onViewChange: () => undefined,
    setRoute: () => undefined, setPins: () => undefined, setCursor: () => undefined, setDetail: () => undefined, setShapes: () => undefined, onMapClick: () => undefined,
    resize: () => undefined, destroy: () => undefined,
  }),
}));

class FakeSocket {
  static instances: FakeSocket[] = [];
  static OPEN = 1;
  readyState = 0; sent: string[] = []; url: string;
  onopen?: () => void; onmessage?: (ev: { data: string }) => void; onclose?: (ev: { code: number }) => void | Promise<void>;
  constructor(url: string) { this.url = url; FakeSocket.instances.push(this); }
  send(data: string) { this.sent.push(data); }
  close() { this.onclose?.({ code: 1000 }); }
  open() { this.readyState = 1; this.onopen?.(); }
  push(msg: unknown) { this.onmessage?.({ data: JSON.stringify(msg) }); }
}

function vehicle(id: number, reg: string, status: string, extra: Partial<LiveVehicle> = {}): LiveVehicle {
  return { id, device_id: id, device_name: `Tracker ${id}`, identifier: `35630704244101${id}`, model: "Teltonika FMC920",
    vehicle: { id: id * 10, registration_number: reg, make: "Isuzu", model: "FVR", vehicle_type: "truck", groups: [1] },
    driver: id === 1 ? { id: 5, name: "Hamisi Juma", phone: "+255712000001" } : null, status, latitude: -6.8 - id / 100,
    longitude: 39.28, speed_kmh: status === "ONLINE_MOVING" ? 42 : 0, heading: 90, satellites: 11, ignition: status !== "ONLINE_STOPPED",
    gps_valid: true, fix_time: new Date().toISOString(), last_packet_at: new Date(Date.now() - 30_000).toISOString(),
    external_voltage: 27.6, battery_voltage: 4.0, gsm_signal: 4, ...extra };
}
const SNAPSHOT = [vehicle(1, "T 123 ABC", "ONLINE_MOVING"), vehicle(2, "T 456 XYZ", "ONLINE_IDLE"), vehicle(3, "T 789 DEF", "OFFLINE")];

let mock: MockAdapter;
beforeEach(() => {
  mock = new MockAdapter(api); setLanguage("en"); FakeSocket.instances = []; markers.current = []; markers.panned = [];
  vi.stubGlobal("WebSocket", FakeSocket);
  useLive.setState({ vehicles: {}, connection: "connecting", needsSnapshot: false });
  mock.onPost("/auth/ws-ticket/").reply(200, { ticket: "t1" });
  mock.onGet("/tracking/live/").reply(200, SNAPSHOT);
  mock.onGet("/settings/map/").reply(200, { tile_url: "", attribution: "", center: [-6.79, 39.2], zoom: 12 });
  mock.onGet("/vehicle-groups/").reply(200, { count: 1, next: null, previous: null, results: [{ id: 1, name: "Upcountry" }] });
});
afterEach(() => { mock.restore(); signOut(); vi.unstubAllGlobals(); });

describe("tracking client", () => {
  it("loads the snapshot, resyncs on connect, applies updates and refetches for unknown trackers", async () => {
    signIn(["tracking.view_live"]);
    const h = startTracking();
    await waitFor(() => expect(FakeSocket.instances).toHaveLength(1));
    expect(FakeSocket.instances[0].url).toContain("/ws/tracking/?ticket=t1&org=1");
    expect(Object.keys(useLive.getState().vehicles)).toHaveLength(3);
    const gets = () => mock.history.get.filter((r) => r.url === "/tracking/live/").length;
    act(() => FakeSocket.instances[0].open());
    await waitFor(() => expect(gets()).toBe(2));                         // resync after (re)connecting
    expect(useLive.getState().connection).toBe("live");
    act(() => FakeSocket.instances[0].push({ type: "updates", updates: [{ device_id: 1, latitude: -6.7, status: "ONLINE_IDLE" }] }));
    expect(useLive.getState().vehicles[1]).toMatchObject({ latitude: -6.7, status: "ONLINE_IDLE", driver: { name: "Hamisi Juma" } });
    act(() => FakeSocket.instances[0].push({ type: "updates", updates: [{ device_id: 99, latitude: -6.7 }] }));
    await waitFor(() => expect(gets()).toBe(3));                         // unknown tracker -> new snapshot
    h.close();
  });

  it("falls back to polling when the socket keeps failing", async () => {
    signIn(["tracking.view_live"]);
    const h = startTracking({ retryBaseMs: 1, pollMs: 60_000 });
    for (let i = 0; i < 4; i += 1) {
      await waitFor(() => expect(FakeSocket.instances.length).toBe(i + 1));
      await act(async () => { await FakeSocket.instances[i].onclose?.({ code: 1006 }); });
    }
    await waitFor(() => expect(useLive.getState().connection).toBe("polling"));
    h.close();
  });
});

describe("live map page", () => {
  it("lists vehicles, filters by status and search, shows details and follows", async () => {
    signIn(["tracking.view_live", "vehicles.view"]);
    renderAt(<AppRoutes />, "/app/live");
    const list = await screen.findByRole("list", { name: "Vehicles" }, { timeout: 4000 });
    await waitFor(() => expect(within(list).getAllByRole("button")).toHaveLength(3));
    expect(markers.current.map((m) => m.status).sort()).toEqual(["OFFLINE", "ONLINE_IDLE", "ONLINE_MOVING"]);
    await userEvent.click(screen.getByRole("button", { name: /Offline 1/ }));
    expect(within(list).getAllByRole("button")).toHaveLength(1);
    expect(markers.current).toHaveLength(1);
    await userEvent.click(screen.getByRole("button", { name: /Offline 1/ }));
    await userEvent.type(screen.getByLabelText("Find vehicle, driver or IMEI"), "hamisi");
    expect(within(list).getAllByRole("button")).toHaveLength(1);
    await userEvent.click(within(list).getByRole("button", { name: /T 123 ABC/ }));
    const panel = screen.getByTestId("vehicle-panel");
    expect(panel).toHaveTextContent("Hamisi Juma");
    expect(panel).toHaveTextContent("42 km/h");
    expect(within(panel).getByTestId("panel-position")).toHaveTextContent("-6.81000, 39.28000");
    await userEvent.click(within(panel).getByRole("button", { name: "Follow" }));
    act(() => useLive.getState().applyUpdates([{ device_id: 1, latitude: -6.75, longitude: 39.3 }]));
    await waitFor(() => expect(markers.panned).toContainEqual({ lat: -6.75, lon: 39.3 }));
    expect(screen.getByText("No map tiles configured (MAP_TILE_URL).")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Geofences" })).not.toBeInTheDocument();   // needs geofences.view
  });

  it("shows a banner when live updates are interrupted", async () => {
    signIn(["tracking.view_live"]);
    renderAt(<AppRoutes />, "/app/live");
    await screen.findByRole("list", { name: "Vehicles" }, { timeout: 4000 });
    act(() => useLive.setState({ connection: "reconnecting" }));
    expect(await screen.findByText("Live updates interrupted. Reconnecting…")).toBeInTheDocument();
  });
});

describe("dashboard", () => {
  it("counts statuses live and lists tracker health", async () => {
    signIn(["dashboard.view", "tracking.view_live"]);
    mock.onGet("/dashboard/summary/").reply(200, { vehicles: 4, vehicles_without_tracker: 1, devices: 3, online_devices: 2,
      by_status: { ONLINE_MOVING: 1, ONLINE_IDLE: 1, OFFLINE: 1 },
      health: { never_connected: [], offline_over_24h: [{ device_id: 3, name: "T 789 DEF", last_packet_at: null }], weak_signal: [], low_tracker_battery: [] } });
    renderAt(<AppRoutes />, "/app");
    expect(await screen.findByRole("heading", { name: "Dashboard" }, { timeout: 4000 })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("card-moving")).toHaveTextContent("1"));
    act(() => useLive.getState().applyUpdates([{ device_id: 2, status: "ONLINE_MOVING" }]));
    await waitFor(() => expect(screen.getByTestId("card-moving")).toHaveTextContent("2"));
    expect(screen.getByTestId("card-idle")).toHaveTextContent("0");
    expect(screen.getByTestId("device-health")).toHaveTextContent("T 789 DEF");
    expect(screen.getByText("1 vehicles have no tracker.")).toBeInTheDocument();
  });

  it("keeps the welcome page for roles without the dashboard", async () => {
    signIn(["users.view"]);
    renderAt(<AppRoutes />, "/app");
    expect(await screen.findByRole("heading", { name: /Welcome/ }, { timeout: 4000 })).toBeInTheDocument();
  });
});
