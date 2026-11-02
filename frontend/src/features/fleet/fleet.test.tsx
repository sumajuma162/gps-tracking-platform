import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MockAdapter from "axios-mock-adapter";
import { api } from "../../services/api";
import { AppRoutes } from "../../app/App";
import { Markdown } from "../../components/Markdown";
import { toPayload } from "../resources/ResourceForm";
import type { FieldDef } from "../resources/types";
import { setLanguage } from "../../i18n";
import { renderAt, signIn, signOut } from "../../test-utils";

let mock: MockAdapter;
beforeEach(() => { mock = new MockAdapter(api); setLanguage("en"); });
afterEach(() => { mock.restore(); signOut(); });

const page = <T,>(results: T[]) => ({ count: results.length, next: null, previous: null, results });
const fmc920 = { id: 1, manufacturer_name: "Teltonika", name: "FMC920", protocol_implemented: true };

describe("form payloads", () => {
  const fields: FieldDef[] = [
    { name: "year", label: "x", type: "number", allowNull: true }, { name: "groups", label: "x", type: "multiselect", options: { path: "/g/", label: () => "" } },
    { name: "sim", label: "x", type: "select", options: { path: "/s/", label: () => "" }, allowNull: true }, { name: "date", label: "x", type: "date", allowNull: true },
    { name: "odometer", label: "x", type: "number" },
    { name: "flag", label: "x", type: "checkbox" }, { name: "name", label: "x" }, { name: "imei", label: "x", createOnly: true },
    { name: "secret", label: "x", type: "password", writeOnlyBlankOnEdit: true },
  ];
  it("converts form strings to API values", () => {
    expect(toPayload(fields, { year: "2021", groups: ["3", "4"], sim: "", date: "", odometer: "", flag: true, name: "  T 1  ", imei: "1", secret: "" }, false))
      .toEqual({ year: 2021, groups: [3, 4], sim: null, date: null, flag: true, name: "T 1", imei: "1", secret: "" });
  });
  it("omits blank non-nullable numbers so the server default applies (e.g. odometer 0)", () => {
    expect(toPayload(fields, { odometer: "" }, false)).not.toHaveProperty("odometer");
    expect(toPayload(fields, { odometer: "12.5" }, false)).toMatchObject({ odometer: 12.5 });
  });
  it("never sends create-only fields or blank secrets when editing", () => {
    const out = toPayload(fields, { year: "", groups: [], sim: "7", date: "2026-01-02", odometer: "", flag: false, name: "x", imei: "1", secret: "" }, true);
    expect(out).not.toHaveProperty("imei");
    expect(out).not.toHaveProperty("secret");
    expect(out).toMatchObject({ year: null, sim: 7, date: "2026-01-02" });
  });
});

describe("markdown", () => {
  it("renders structure and never injects HTML", () => {
    const { container } = render(<Markdown text={"## Step\n\n1. Set `5027`\n2. **Save**\n\n<img src=x onerror=alert(1)>"} />);
    expect(container.querySelector("h4")).toHaveTextContent("Step");
    expect(container.querySelectorAll("ol li")).toHaveLength(2);
    expect(container.querySelector("code")).toHaveTextContent("5027");
    expect(container.querySelector("img")).toBeNull();
    expect(container).toHaveTextContent("<img src=x onerror=alert(1)>");
  });
});

describe("trackers page", () => {
  beforeEach(() => {
    mock.onGet("/devices/").reply(200, page([{ id: 9, name: "Truck 1", identifier: "356307042441013", model_name: "Teltonika FMC920",
      is_active: true, inventory_status: "installed", current_vehicle: { id: 3, registration_number: "T 123 ABC" }, last_packet_at: null }]));
    mock.onGet("/device-catalog/models/").reply(200, page([fmc920]));
    mock.onGet("/sims/").reply(200, page([]));
  });

  it("lists trackers with their vehicle and connection state", async () => {
    signIn(["devices.view"]);
    renderAt(<AppRoutes />, "/app/devices");
    expect((await screen.findAllByText("Truck 1")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("T 123 ABC").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Not connected yet").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Add" })).not.toBeInTheDocument(); // view-only role
  });

  it("adds trackers through the wizard, not a generic form", async () => {
    signIn(["devices.view", "devices.manage"]);
    renderAt(<AppRoutes />, "/app/devices");
    expect(await screen.findByRole("link", { name: "Add tracker" })).toHaveAttribute("href", "/app/devices/new");
    expect(screen.queryByRole("button", { name: "Add" })).not.toBeInTheDocument();
  });

  it("previews a CSV import, then imports only the valid rows", async () => {
    signIn(["devices.view", "devices.manage"]);
    const rows = [{ line: 2, identifier: "356307042441013", name: "A", ok: true, errors: [] },
      { line: 3, identifier: "1", name: "B", ok: false, errors: ["An IMEI has exactly 15 digits."] }];
    mock.onPost("/devices/import/").reply((cfg) => JSON.parse(cfg.data).commit
      ? [201, { committed: true, created: 1, valid: 1, invalid: 1, rows }]
      : [200, { committed: false, created: 0, valid: 1, invalid: 1, rows }]);
    renderAt(<AppRoutes />, "/app/devices");
    await userEvent.click(await screen.findByRole("button", { name: "Import CSV" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Import 0 trackers" })).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Or paste the CSV"), "model,identifier,name");
    await userEvent.click(within(dialog).getByRole("button", { name: "Check file" }));
    expect(await within(dialog).findByText("1 ready to import, 1 with problems (skipped).")).toBeInTheDocument();
    expect(within(dialog).getByText("An IMEI has exactly 15 digits.")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Import 1 trackers" }));
    await waitFor(() => expect(mock.history.post.filter((r) => JSON.parse(r.data).commit)).toHaveLength(1));
  });
});

describe("vehicles page", () => {
  it("validates required fields before calling the server", async () => {
    signIn(["vehicles.view", "vehicles.manage"]);
    mock.onGet("/vehicles/").reply(200, page([]));
    mock.onGet("/vehicle-groups/").reply(200, page([]));
    renderAt(<AppRoutes />, "/app/vehicles");
    expect(await screen.findByText("No vehicles yet. Add your first vehicle.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add" }));
    expect(await within(dialog).findAllByText("Required")).not.toHaveLength(0);
    expect(mock.history.post).toHaveLength(0);
  });
});

describe("clone model", () => {
  it("shows the server's reason under the slug field", async () => {
    signIn([], true);
    mock.onGet("/device-catalog/models/").reply(200, page([{ id: 1, manufacturer_name: "Teltonika", name: "FMC920", slug: "teltonika-fmc920",
      protocol_name: "teltonika", protocol_implemented: true, transports: ["tcp"], default_port: 5027, verified: false, status: "active", io_profile: 1 }]));
    mock.onPost("/device-catalog/models/1/clone/").reply(400, { error: { code: "validation_error", message: "Some fields are invalid.",
      details: { slug: ["A model with this slug already exists."] } } });
    renderAt(<AppRoutes />, "/app/device-catalog");
    await userEvent.click((await screen.findAllByRole("button", { name: "Clone" }))[0]);   // card + table both render
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Clone" }));
    expect(await within(dialog).findByText("A model with this slug already exists.")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Slug")).toHaveAttribute("aria-invalid", "true");
  });
});
