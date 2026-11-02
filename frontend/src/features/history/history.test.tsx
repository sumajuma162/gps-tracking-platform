import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MockAdapter from "axios-mock-adapter";
import { api } from "../../services/api";
import { AppRoutes } from "../../app/App";
import { setLanguage } from "../../i18n";
import { renderAt, signIn, signOut } from "../../test-utils";
import { advance, indexAt, sampleAt, type RoutePoint } from "./playback";

const calls: { route: unknown[]; routeOptions: { highlights?: unknown[][]; arrows?: boolean }[]; cursor: { lat: number; lon: number }[]; pins: unknown[]; pans: unknown[] } =
  { route: [], routeOptions: [], cursor: [], pins: [], pans: [] };
vi.mock("../../maps", () => ({
  createMap: () => ({
    setMarkers: () => undefined, fitTo: () => undefined, panTo: (p: unknown) => { calls.pans.push(p); },
    onMarkerClick: () => undefined, onViewChange: () => undefined, resize: () => undefined, destroy: () => undefined,
    setRoute: (r: unknown, o: { highlights?: unknown[][]; arrows?: boolean } = {}) => { calls.route.push(r); calls.routeOptions.push(o); },
    setPins: (p: unknown) => { calls.pins.push(p); }, setDetail: () => undefined, setShapes: () => undefined, onMapClick: () => undefined,
    setCursor: (c: { lat: number; lon: number } | null) => { if (c) calls.cursor.push(c); },
  }),
}));

const T0 = Date.UTC(2026, 8, 30, 6, 0, 0) / 1000;
const POINTS: RoutePoint[] = [[T0, -6.80, 39.20, 40, 90, true], [T0 + 600, -6.80, 39.25, 50, 90, true],
  [T0 + 900, -6.80, 39.25, 0, null, false], [T0 + 2700, -6.80, 39.25, 0, null, false], [T0 + 3600, -6.79, 39.30, 45, 80, true]];
const HISTORY = { speeding: [[T0 + 100, T0 + 500, 92]], points: POINTS, speed: [[T0, 40], [T0 + 600, 50], [T0 + 900, 0], [T0 + 2700, 0], [T0 + 3000, 45]], stops: [{ start: new Date((T0 + 900) * 1000).toISOString(), end: new Date((T0 + 2700) * 1000).toISOString(),
  duration_s: 1800, latitude: -6.8, longitude: 39.25, engine_off: true }],
  summary: { points: 4321, returned_points: 5, distance_km: 11.6, max_speed_kmh: 50, avg_moving_speed_kmh: 23, driving_s: 1800, idle_s: 120,
    stopped_s: 1800, speeding_limit_kmh: 80,
    start: new Date(T0 * 1000).toISOString(), end: new Date((T0 + 3600) * 1000).toISOString() } };

describe("playback maths", () => {
  it("offers the spec speeds plus faster ones for long ranges", async () => {
    const { RATES } = await import("./playback");
    expect([...RATES]).toEqual([0.5, 1, 2, 4, 8, 16, 60, 300]);
  });
  it("finds the segment and interpolates", () => {
    expect(indexAt(POINTS, T0 - 5)).toBe(0);
    expect(indexAt(POINTS, T0 + 600)).toBe(1);
    expect(indexAt(POINTS, T0 + 899)).toBe(1);
    expect(indexAt(POINTS, T0 + 99_999)).toBe(4);
    const mid = sampleAt(POINTS, T0 + 300)!;
    expect(mid.lon).toBeCloseTo(39.225);
    expect(mid.speed).toBe(40);
    expect(sampleAt(POINTS, T0 + 1800)!.lon).toBeCloseTo(39.25);            // standing still during the stop
    expect(sampleAt([], T0)).toBeNull();
  });
  it("advances at the chosen rate and stops at the end", () => {
    expect(advance(T0, 1000, 60, T0 + 3600)).toBe(T0 + 60);
    expect(advance(T0 + 3590, 1000, 60, T0 + 3600)).toBe(T0 + 3600);
  });
});

let mock: MockAdapter;
beforeEach(() => {
  mock = new MockAdapter(api); setLanguage("en"); calls.route = []; calls.routeOptions = []; calls.cursor = []; calls.pins = []; calls.pans = [];
  mock.onGet("/vehicles/").reply(200, { count: 1, next: null, previous: null, results: [{ id: 7, registration_number: "T 123 ABC" }] });
  mock.onGet("/settings/map/").reply(200, { tile_url: "", attribution: "", center: [-6.79, 39.2], zoom: 12 });
  mock.onGet("/tracking/history/").reply(200, HISTORY);
});
afterEach(() => { mock.restore(); signOut(); });

const PATH = `/app/history?vehicle=7&from=${new Date(T0 * 1000).toISOString()}&to=${new Date((T0 + 3600) * 1000).toISOString()}`;

describe("history page", () => {
  it("draws the route, lists stops, seeks and plays", { timeout: 20000 }, async () => {
    signIn(["routes.view_history", "vehicles.view", "reports.export"]);
    renderAt(<AppRoutes />, PATH);
    const summary = await screen.findByTestId("history-summary", {}, { timeout: 4000 });
    expect(summary).toHaveTextContent("11.6 km");
    expect(summary).toHaveTextContent("30 min");
    expect(summary).toHaveTextContent("23 km/h");                                   // average moving speed
    expect(summary).toHaveTextContent("2 min");                                     // idling
    await waitFor(() => expect(calls.route.at(-1)).toHaveLength(5));
    expect(calls.routeOptions.at(-1)?.arrows).toBe(true);
    expect(calls.routeOptions.at(-1)?.highlights).toHaveLength(1);                  // the speeding stretch
    expect(screen.getByTestId("timeline-ticks").children).toHaveLength(2);           // a stop tick and a speeding band
    expect(screen.getByLabelText("Speed")).toHaveValue("16");                        // spec speeds: 0.5x-16x
    expect((calls.pins.at(-1) as { kind: string }[]).map((p) => p.kind)).toEqual(["start", "stop", "end"]);
    expect(mock.history.get.find((r) => r.url === "/tracking/history/")?.params).toMatchObject({ vehicle: "7", zoom: 15 });

    await userEvent.click(within(screen.getByRole("list", { name: "Stops" })).getByRole("button"));
    expect(calls.pans.at(-1)).toEqual({ lat: -6.8, lon: 39.25 });
    expect(screen.getByTestId("playback-speed")).toHaveTextContent("0 km/h");      // parked at the stop
    expect(screen.getByTestId("playback-coordinates")).toHaveTextContent("-6.80000, 39.25000");
    expect(screen.getByTestId("playback-panel")).toHaveTextContent("Ignition: Off");
    expect(calls.cursor.at(-1)).toMatchObject({ lat: -6.8, lon: 39.25 });

    await userEvent.selectOptions(screen.getByLabelText("Speed"), "300");
    const before = calls.cursor.length;
    await userEvent.click(screen.getByRole("button", { name: "Play" }));
    await waitFor(() => expect(calls.cursor.length).toBeGreaterThan(before + 3));
    // The 30-minute stop takes 6 s at 300x; then the cursor moves on.
    await waitFor(() => expect(calls.cursor.at(-1)!.lon).toBeGreaterThan(39.25), { timeout: 9000 });
    await userEvent.click(screen.getByRole("button", { name: "Pause" }));
    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(calls.cursor.at(-1)).toMatchObject({ lat: -6.8, lon: 39.2 });           // back to the start
    expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Restart" }));
    expect(await screen.findByRole("button", { name: "Pause" })).toBeInTheDocument();  // playing again from the start
    await userEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(screen.getByText("Drawn with 5 of 4321 recorded points.")).toBeInTheDocument();
  });

  it("downloads a file when allowed, and hides downloads otherwise", async () => {
    signIn(["routes.view_history", "vehicles.view", "reports.export"]);
    mock.onGet("/tracking/history/export/").reply(200, new Blob(["time_utc\n"]), { "content-disposition": 'attachment; filename="T-123-ABC_2026-09-30.gpx"' });
    const create = vi.fn(() => "blob:route"); URL.createObjectURL = create; URL.revokeObjectURL = vi.fn();
    renderAt(<AppRoutes />, PATH);
    await userEvent.click(await screen.findByRole("button", { name: "GPX" }, { timeout: 4000 }));
    await waitFor(() => expect(create).toHaveBeenCalled());
    expect(mock.history.get.find((r) => r.url === "/tracking/history/export/")?.params).toMatchObject({ type: "gpx", vehicle: "7" });
    signOut();
  });

  it("offers no downloads to roles that cannot export", async () => {
    signIn(["routes.view_history", "vehicles.view"]);
    renderAt(<AppRoutes />, PATH);
    await screen.findByTestId("history-summary", {}, { timeout: 4000 });
    expect(screen.queryByRole("button", { name: "CSV" })).not.toBeInTheDocument();
  });

  it("asks for a vehicle first", async () => {
    signIn(["routes.view_history", "vehicles.view"]);
    renderAt(<AppRoutes />, "/app/history");
    expect(await screen.findByText("Choose a vehicle and a period.", {}, { timeout: 4000 })).toBeInTheDocument();
    expect(mock.history.get.some((r) => r.url === "/tracking/history/")).toBe(false);
    act(() => undefined);
  });
});
