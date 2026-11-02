import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MockAdapter from "axios-mock-adapter";
import { api } from "../../services/api";
import { AppRoutes } from "../../app/App";
import { setLanguage } from "../../i18n";
import { renderAt, signIn, signOut } from "../../test-utils";
import type { LatLon, ShapeSpec } from "../../maps/types";

const map: { shapes: ShapeSpec[]; click: ((p: LatLon) => void) | null } = { shapes: [], click: null };
vi.mock("../../maps", () => ({
  createMap: () => ({
    setMarkers: () => undefined, fitTo: () => undefined, panTo: () => undefined, onMarkerClick: () => undefined,
    onViewChange: () => undefined, resize: () => undefined, destroy: () => undefined, setRoute: () => undefined,
    setPins: () => undefined, setCursor: () => undefined, setDetail: () => undefined,
    setShapes: (s: ShapeSpec[]) => { map.shapes = s; }, onMapClick: (h: ((p: LatLon) => void) | null) => { map.click = h; },
  }),
}));

let mock: MockAdapter;
const page = <T,>(results: T[]) => ({ count: results.length, next: null, previous: null, results });
const DEPOT = { id: 1, name: "Kurasini depot", description: "", kind: "circle", center_lat: -6.85, center_lon: 39.28, radius_m: 250,
  polygon: [], dwell_minutes: 30, color: "#2563eb", is_active: true };
beforeEach(() => {
  mock = new MockAdapter(api); setLanguage("en"); map.shapes = []; map.click = null;
  mock.onGet("/settings/map/").reply(200, { tile_url: "", attribution: "", center: [-6.79, 39.2], zoom: 12 });
  mock.onGet("/geofences/").reply(200, page([DEPOT]));
  mock.onGet("/vehicles/").reply(200, page([{ id: 7, registration_number: "T 123 ABC" }]));
});
afterEach(() => { mock.restore(); signOut(); });

describe("geofences", () => {
  it("draws a polygon by clicking the map and saves it", async () => {
    signIn(["geofences.view", "geofences.manage"]);
    mock.onPost("/geofences/").replyOnce(400, { error: { code: "validation_error", message: "Some fields are invalid.",
      details: { polygon: ["Draw at least 3 points."] } } }).onPost("/geofences/").reply(201, { id: 2 });
    renderAt(<AppRoutes />, "/app/geofences");
    expect(await screen.findByText("Kurasini depot", {}, { timeout: 4000 })).toBeInTheDocument();
    await waitFor(() => expect(map.shapes.map((s) => s.label)).toEqual(["Kurasini depot"]));
    await userEvent.click(screen.getByRole("button", { name: "New geofence" }));
    const editor = screen.getByTestId("geofence-editor");
    await userEvent.type(within(editor).getByLabelText("Name *"), "Port area");
    act(() => { map.click?.({ lat: -6.82, lon: 39.28 }); map.click?.({ lat: -6.82, lon: 39.30 }); });
    expect(within(editor).getByRole("status")).toHaveTextContent("(2 so far)");
    await userEvent.click(within(editor).getByRole("button", { name: "Save changes" }));
    expect(await within(editor).findByText("Draw at least 3 points.")).toBeInTheDocument();
    act(() => { map.click?.({ lat: -6.84, lon: 39.30 }); map.click?.({ lat: -6.99, lon: 39.99 }); });
    await userEvent.click(within(editor).getByRole("button", { name: "Undo point" }));
    expect(map.shapes.find((s) => s.id === "draft")?.polygon).toHaveLength(3);
    await userEvent.click(within(editor).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(mock.history.post).toHaveLength(2));
    expect(JSON.parse(mock.history.post[1].data)).toEqual({ name: "Port area", kind: "polygon", color: "#2563eb", dwell_minutes: null,
      polygon: [[-6.82, 39.28], [-6.82, 39.3], [-6.84, 39.3]] });
  });

  it("is read-only without the manage permission", async () => {
    signIn(["geofences.view"]);
    renderAt(<AppRoutes />, "/app/geofences");
    expect(await screen.findByText("Kurasini depot", {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New geofence" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  });
});

describe("trips", () => {
  it("lists trips with a route link and marks delayed ones", async () => {
    signIn(["trips.view", "routes.view_history"]);
    mock.onGet("/trips/").reply(200, page([{ id: 3, vehicle: 7, vehicle_registration: "T 123 ABC", driver_name: "Asha Said",
      start_time: "2026-10-01T06:00:00Z", end_time: "2026-10-01T06:15:00Z", duration_s: 900, distance_km: 13.2,
      distance_source: "gps", max_speed_kmh: 95, delayed: true }]));
    renderAt(<AppRoutes />, "/app/trips");
    const row = (await screen.findAllByText("T 123 ABC", {}, { timeout: 4000 }))[0].closest("tr, li") as HTMLElement;
    expect(within(row).getByText("From delayed data")).toBeInTheDocument();
    expect(within(row).getByText("13.2 km")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "View route" })).toHaveAttribute("href",
      "/app/history?vehicle=7&from=2026-10-01T06%3A00%3A00Z&to=2026-10-01T06%3A15%3A00Z");
    expect(screen.queryByPlaceholderText("Search")).not.toBeInTheDocument();    // the API has no text search
  });
});
