import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MockAdapter from "axios-mock-adapter";
import { api } from "../../services/api";
import { AppRoutes } from "../../app/App";
import { imeiProblem, luhnValid } from "../../utils/imei";
import { toMappings } from "../platform/IoProfileEditor";
import { setLanguage } from "../../i18n";
import { renderAt, signIn, signOut } from "../../test-utils";

let mock: MockAdapter;
beforeEach(() => { mock = new MockAdapter(api); setLanguage("en"); vi.stubGlobal("WebSocket", undefined); });
afterEach(() => { mock.restore(); signOut(); vi.unstubAllGlobals(); });
const page = <T,>(results: T[]) => ({ count: results.length, next: null, previous: null, results });

const MODELS = [
  { id: 1, manufacturer_name: "Teltonika", name: "FMC920", slug: "teltonika-fmc920", protocol_name: "teltonika", protocol_implemented: true,
    identifier_type: "imei", transports: ["tcp", "udp"], default_port: 5027, verified: false },
  { id: 4, manufacturer_name: "Concox", name: "GT06", slug: "gt06-generic", protocol_name: "gt06", protocol_implemented: false,
    identifier_type: "imei", transports: ["tcp"], default_port: 5023, verified: false },
];
const DIAG = {
  device: { id: 9, name: "Truck 9", identifier: "356307042441013", is_active: true, model: "Teltonika FMC920" },
  stages: [{ key: "registered", done: true }, { key: "connected", done: true }, { key: "first_packet", done: true },
    { key: "valid_fix", done: true }, { key: "io_mapped", done: true }],
  current_stage: null, connection: { connected_at: "2026-10-01T10:00:00Z", disconnected_at: null, peer: "41.59.1.2:5000", transport: "tcp",
    port: 5027, packets: 3, records: 7, close_reason: "" },
  latest: { device_time: "2026-10-01T10:00:05Z", latitude: -6.8161, longitude: 39.2804, speed_kmh: 42, satellites: 11, gps_valid: true,
    mapped: { ignition: true, external_voltage: 27.65 }, sensors: {}, events: [], raw_io: {}, lag_seconds: 2 },
  last_packet_at: "2026-10-01T10:00:06Z", possible_typos: [],
};

describe("IMEI checks", () => {
  it("matches the server's rules", () => {
    expect(luhnValid("356307042441013")).toBe(true);
    expect(imeiProblem("3563 0704 2441 013")).toBeNull();
    expect(imeiProblem("356307042441014")).toBe("checksum");
    expect(imeiProblem("35630704244101")).toBe("length");
    expect(imeiProblem("ABC")).toBe("digits");
    expect(imeiProblem(" ")).toBe("empty");
  });
});

describe("add tracker wizard", () => {
  beforeEach(() => {
    signIn(["devices.view", "devices.manage", "sims.view", "vehicles.view"]);
    mock.onGet("/device-catalog/models/").reply(200, page(MODELS));
    mock.onGet("/sims/").reply(200, page([{ id: 5, phone_number: "+255712000999", operator: "Vodacom", apn: "internet", device_name: null }]));
    mock.onGet("/vehicles/").reply(200, page([{ id: 3, registration_number: "T 123 ABC", make: "Isuzu", model: "FVR", current_device: null }]));
  });

  async function toIdentity() {
    renderAt(<AppRoutes />, "/app/devices/new");
    expect(await screen.findByLabelText(/Concox GT06/)).toBeDisabled();       // protocol still in development
    await userEvent.click(screen.getByLabelText(/Teltonika FMC920/));
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
  }

  it("blocks a mistyped IMEI before calling the server", async () => {
    await toIdentity();
    await userEvent.type(screen.getByLabelText("IMEI or identifier *"), "356307042441014");
    await userEvent.type(screen.getByLabelText("Name *"), "Truck 9");
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect((await screen.findAllByText(/check digit is wrong/)).length).toBeGreaterThan(0);
    expect(mock.history.post).toHaveLength(0);
  });

  it("registers SIM, tracker and vehicle, then shows the live connection test", async () => {
    mock.onPost("/sims/").reply(201, { id: 77 });
    mock.onPost("/devices/").reply(201, { id: 9 });
    mock.onPost("/devices/9/assign/").reply(200, {});
    mock.onGet("/devices/9/setup-instructions/").reply(200, { markdown: "## Connect\\n\\n1. Set `5027`", server_host: "gps.demo.co.tz", port: 5027, transport: "tcp", apn: "internet" });
    mock.onGet("/devices/9/diagnostics/").reply(200, DIAG);
    await toIdentity();
    await userEvent.type(screen.getByLabelText("IMEI or identifier *"), "3563 0704 2441 013");
    await userEvent.type(screen.getByLabelText("Name *"), "Truck 9");
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await userEvent.click(screen.getByLabelText("Add a new SIM card"));
    await userEvent.type(screen.getByLabelText("Phone number *"), "+255 754 111 222");
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await userEvent.selectOptions(screen.getByLabelText("Vehicle"), "3");
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await userEvent.click(screen.getByRole("button", { name: "Register tracker" }));
    expect(await screen.findByText("gps.demo.co.tz")).toBeInTheDocument();
    const panel = await screen.findByTestId("diagnostics");
    expect(await within(panel).findByText("Everything works. The tracker is reporting.")).toBeInTheDocument();
    expect(within(panel).getByTestId("diag-external_voltage")).toHaveTextContent("27.65 V");
    expect(within(panel).getByText("Checking every 2 seconds")).toBeInTheDocument();   // no WebSocket: polling fallback
    const posts = mock.history.post.map((r) => [r.url, JSON.parse(r.data ?? "{}")]);
    expect(posts[0]).toEqual(["/sims/", { phone_number: "+255 754 111 222", operator: "", apn: "internet" }]);
    expect(posts[1][1]).toMatchObject({ device_model: 1, identifier: "356307042441013", name: "Truck 9", sim: 77 });
    expect(posts[2]).toEqual(["/devices/9/assign/", { vehicle_id: 3 }]);
  });

  it.each([
    ["with a field error", { identifier: ["This identifier is already registered on the platform."] }],
    ["with only the error code", undefined],
  ])("returns to the identity step when the server rejects the IMEI (%s)", async (_label, details) => {
    mock.onPost("/devices/").reply(400, { error: { code: "identifier_taken", message: "This identifier is already registered on the platform.",
      ...(details ? { details } : {}) } });
    await toIdentity();
    await userEvent.type(screen.getByLabelText("IMEI or identifier *"), "356307042441013");
    await userEvent.type(screen.getByLabelText("Name *"), "Truck 9");
    for (let i = 0; i < 3; i += 1) await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await userEvent.click(screen.getByRole("button", { name: "Register tracker" }));
    expect(await screen.findByLabelText("IMEI or identifier *")).toBeInTheDocument();
    expect(screen.getAllByText("This identifier is already registered on the platform.").length).toBeGreaterThan(0);
  });
});

describe("diagnostics", () => {
  it("warns about a likely IMEI typo while waiting for the connection", async () => {
    signIn(["devices.view", "devices.manage"]);
    mock.onGet("/devices/").reply(200, page([{ id: 9, name: "Truck 9", identifier: "356307042441013", model_name: "Teltonika FMC920",
      is_active: true, inventory_status: "in_stock", current_vehicle: null, last_packet_at: null }]));
    mock.onGet("/devices/9/diagnostics/").reply(200, { ...DIAG, current_stage: "connected", connection: null, latest: null,
      stages: DIAG.stages.map((s, i) => ({ ...s, done: i === 0 })),
      possible_typos: [{ identifier: "356307042441014", last_seen_at: "2026-10-01T09:58:00Z", attempts: 6 }] });
    renderAt(<AppRoutes />, "/app/devices");
    await userEvent.click((await screen.findAllByRole("button", { name: "Connection test" }))[0]);
    const panel = await screen.findByTestId("diagnostics");
    expect(await within(panel).findByText(/No connection yet/)).toBeInTheDocument();
    expect(within(panel).getByRole("alert")).toHaveTextContent("356307042441014");
  });
});

describe("IO mapping editor", () => {
  it("converts rows to mappings", () => {
    expect(toMappings([
      { io_id: "66", kind: "field", target: "external_voltage", scale: "0.01", offset: "", unit: "V", events: "" },
      { io_id: "253", kind: "event", target: "green", scale: "", offset: "", unit: "", events: "1=harsh_acceleration, 2 = harsh_braking, junk" },
    ])).toEqual([
      { io_id: 66, kind: "field", target: "external_voltage", scale: 0.01, unit: "V" },
      { io_id: 253, kind: "event", target: "green", event_map: { "1": "harsh_acceleration", "2": "harsh_braking" } },
    ]);
  });

  it("previews a change and saves it as a new version", async () => {
    signIn([], true);
    const profile = { id: 2, name: "Teltonika FMB920", verified: false, used_by: ["Teltonika FMB920"],
      current: { version: 1, notes: "", created_at: "2026-10-01T08:00:00Z",
        mappings: [{ io_id: 239, kind: "field", target: "ignition" }, { io_id: 66, kind: "field", target: "external_voltage", scale: 0.001, unit: "V" }] } };
    mock.onGet("/device-catalog/io-profiles/2/").reply(200, profile);
    mock.onGet("/device-catalog/io-profiles/2/versions/").reply(200, [{ id: 11, version: 1, notes: "", created_by_email: null, created_at: "2026-10-01T08:00:00Z" }]);
    mock.onPost("/device-catalog/io-profiles/2/preview/").reply(200, { source: "sample", io: { "66": 1265, "239": 1 },
      current: { fields: { ignition: true, external_voltage: 1.265 }, sensors: {}, events: [], unmapped: {} },
      draft: { fields: { ignition: true, external_voltage: 12.65 }, sensors: {}, events: [], unmapped: {} } });
    mock.onPatch("/device-catalog/io-profiles/2/").reply(200, profile);
    renderAt(<AppRoutes />, "/app/device-catalog/io-profiles/2");
    const scale = await screen.findByLabelText("Scale for IO 66");
    await userEvent.clear(scale);
    await userEvent.type(scale, "0.01");
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    const changed = await screen.findByTestId("draft-66");
    expect(changed).toHaveTextContent("12.65");
    expect(changed.className).toContain("bg-marking-soft");                     // highlighted as changed
    expect(screen.getByTestId("draft-239").className).not.toContain("bg-marking-soft");
    await userEvent.type(screen.getByLabelText("What changed"), "10 mV steps");
    await userEvent.click(screen.getByRole("button", { name: "Save as new version" }));
    await waitFor(() => expect(mock.history.patch).toHaveLength(1));
    expect(JSON.parse(mock.history.patch[0].data)).toEqual({ notes: "10 mV steps", mappings: [
      { io_id: 239, kind: "field", target: "ignition" }, { io_id: 66, kind: "field", target: "external_voltage", scale: 0.01, unit: "V" }] });
  });
});

describe("unregistered devices", () => {
  it("registers an unknown tracker into an organization with a model of its protocol", async () => {
    signIn([], true);
    mock.onGet("/platform/unregistered-devices/").reply(200, page([{ id: 3, identifier: "490154203237518", protocol: "teltonika", transport: "tcp",
      port: 5027, last_peer: "41.59.1.2:5000", first_seen_at: "2026-10-01T09:00:00Z", last_seen_at: "2026-10-01T09:05:00Z", attempts: 4, sample_hex: "000f" }]));
    mock.onGet("/organizations/").reply(200, page([{ id: 7, name: "Demo Logistics Ltd" }]));
    mock.onGet("/device-catalog/models/").reply(200, page(MODELS));
    mock.onPost("/platform/unregistered-devices/3/register/").reply(201, { organization: "Demo Logistics Ltd" });
    renderAt(<AppRoutes />, "/app/unregistered");
    await userEvent.click(await screen.findByRole("button", { name: "Register" }));
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(within(dialog).getAllByRole("option").length).toBeGreaterThan(2));
    expect(within(dialog).queryByRole("option", { name: "Concox GT06" })).not.toBeInTheDocument();   // other protocol
    await userEvent.selectOptions(within(dialog).getByLabelText("Organization *"), "7");
    await userEvent.selectOptions(within(dialog).getByLabelText("Tracker model *"), "1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Register" }));
    await waitFor(() => expect(mock.history.post).toHaveLength(1));
    expect(JSON.parse(mock.history.post[0].data)).toEqual({ organization: 7, device_model: 1, name: "Tracker 7518" });
  });
});
