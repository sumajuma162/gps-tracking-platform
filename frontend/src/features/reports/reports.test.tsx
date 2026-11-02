import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MockAdapter from "axios-mock-adapter";
import { api } from "../../services/api";
import { AppRoutes } from "../../app/App";
import { setLanguage } from "../../i18n";
import { renderAt, signIn, signOut } from "../../test-utils";
import { DashboardAlerts } from "../alerts/DashboardAlerts";

let mock: MockAdapter;
const page = <T,>(results: T[]) => ({ count: results.length, next: null, previous: null, results });
const TRIPS = { key: "trips", title: "Trips", columns: [{ key: "vehicle", label: "Vehicle", kind: "text" }, { key: "distance", label: "Distance", kind: "km" }],
  filters: ["vehicle", "start", "end"], chart: null };
beforeEach(() => {
  mock = new MockAdapter(api); setLanguage("en");
  mock.onGet("/reports/").reply(200, { reports: [TRIPS, { ...TRIPS, key: "distance", title: "Distance" }] });
  mock.onGet(/\/(vehicles|vehicle-groups|drivers)\//).reply(200, page([{ id: 7, registration_number: "T 123 ABC" }]));
  mock.onGet("/reports/trips/").reply(200, { columns: TRIPS.columns, rows: [{ vehicle: "T 123 ABC", distance: 25.5 }], count: 1, page: 1, page_size: 50,
    chart: [{ label: "T 123 ABC", value: 25.5 }] });
});
afterEach(() => { mock.restore(); signOut(); });

describe("reports", () => {
  it("runs a report, exports directly or through a background job", async () => {
    signIn(["reports.view", "reports.export", "vehicles.view"]);
    const create = vi.fn(() => "blob:r"); URL.createObjectURL = create; URL.revokeObjectURL = vi.fn();
    renderAt(<AppRoutes />, "/app/reports");
    await userEvent.click(await screen.findByRole("button", { name: "Run report" }, { timeout: 4000 }));
    expect(within(await screen.findByTestId("report-table")).getByText("25.5")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Chart" })).toBeInTheDocument();
    expect(mock.history.get.find((r) => r.url === "/reports/trips/")!.params).toMatchObject({ page: 1, page_size: 50 });

    mock.onPost("/reports/trips/export/").replyOnce(200, new Blob(["Vehicle\n"]), { "content-disposition": 'attachment; filename="trips_1.csv"' })
      .onPost("/reports/trips/export/").replyOnce(202, new Blob([JSON.stringify({ id: 4, status: "queued", progress: 0, downloadable: false, filename: "", error: "" })]));
    await userEvent.click(screen.getByRole("button", { name: "CSV" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    mock.onGet("/report-jobs/4/").reply(200, { id: 4, status: "done", progress: 100, downloadable: true, filename: "trips_1.xlsx", error: "" });
    await userEvent.click(screen.getByRole("button", { name: "Excel" }));
    expect(await screen.findByText(/Preparing the export/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /Download trips_1.xlsx/ }, { timeout: 4000 })).toBeInTheDocument();
    expect(JSON.parse(mock.history.post[1].data)).toMatchObject({ format: "xlsx", filters: { start: expect.any(String) } });
  });

  it("hides exports from roles that cannot export", async () => {
    signIn(["reports.view", "vehicles.view"]);
    renderAt(<AppRoutes />, "/app/reports");
    expect(await screen.findByRole("button", { name: "Run report" }, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "PDF" })).not.toBeInTheDocument();
  });
});

it("dashboard shows today's alerts and the open ones", async () => {
  signIn(["alerts.view"]);
  mock.onGet("/alerts/counts/").reply(200, { open: 2, critical_open: 1, today: 5 });
  mock.onGet("/alerts/").reply(200, page([{ id: 1, type: "crash", severity: "critical", message: "x", message_params: { vehicle: "T 9" },
    status: "new", occurrences: 1, delayed: false, occurred_at: "2026-10-01T06:00:00Z", last_occurred_at: "2026-10-01T06:00:00Z" }]));
  renderAt(<DashboardAlerts />, "/");
  await waitFor(() => expect(screen.getByTestId("alerts-today")).toHaveTextContent("5"), { timeout: 4000 });
  expect(await screen.findByText("T 9: possible crash")).toBeInTheDocument();
});
