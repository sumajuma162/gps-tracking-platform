import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MockAdapter from "axios-mock-adapter";
import { api } from "../../services/api";
import { AppRoutes } from "../../app/App";
import { setLanguage } from "../../i18n";
import { renderAt, signIn, signOut } from "../../test-utils";
import { toast } from "../../components/Toast";
import { ruleFromForm, ruleToForm } from "./AlertRulesPage";

class FakeSocket {
  static last: FakeSocket | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(public url: string) { FakeSocket.last = this; }
  close() { /* closed */ }
}

let mock: MockAdapter;
const page = <T,>(results: T[]) => ({ count: results.length, next: null, previous: null, results });
const ALERT = { id: 5, type: "overspeed", severity: "warning", message: "T 1 exceeded", status: "new", occurrences: 3, delayed: true,
  message_params: { vehicle: "T 123 ABC", limit: 80, value: 95 }, occurred_at: "2026-10-01T06:00:00Z", last_occurred_at: "2026-10-01T06:10:00Z",
  vehicle: 7, vehicle_registration: "T 123 ABC", rule_name: "Speeding", notes: "", acknowledged_by_email: null, resolved_by_email: null };
beforeEach(() => {
  mock = new MockAdapter(api); setLanguage("en");
  vi.stubGlobal("WebSocket", FakeSocket);
  mock.onPost("/auth/ws-ticket/").reply(200, { ticket: "t1" });
  mock.onGet("/notifications/unread/").reply(200, { unread: 2 });
  mock.onGet("/notifications/").reply(200, page([{ id: 1, title: "T 123 ABC exceeded 80 km/h", severity: "warning", read_at: null, created_at: "2026-10-01T06:10:00Z", alert: 5 }]));
  mock.onGet("/alerts/").reply(200, page([ALERT]));
});
afterEach(() => { mock.restore(); signOut(); vi.unstubAllGlobals(); });

describe("alerts", () => {
  it("shows alerts in the reader's language and acknowledges with notes", async () => {
    signIn(["alerts.view", "alerts.acknowledge", "notifications.view"]);
    mock.onPost("/alerts/5/acknowledge/").reply(200, { ...ALERT, status: "acknowledged" });
    renderAt(<AppRoutes />, "/app/alerts");
    const item = (await screen.findByText("T 123 ABC exceeded 80 km/h (top 95 km/h)", {}, { timeout: 4000 })).closest("li") as HTMLElement;
    expect(within(item).getByText("Delayed data")).toBeInTheDocument();
    expect(within(item).getByText("×3")).toBeInTheDocument();
    await userEvent.click(within(item).getByRole("button", { name: "Acknowledge" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Notes"), "Called the driver");
    await userEvent.click(within(dialog).getByRole("button", { name: "Acknowledge" }));
    await waitFor(() => expect(JSON.parse(mock.history.post.find((r) => r.url === "/alerts/5/acknowledge/")!.data)).toEqual({ notes: "Called the driver" }));
    setLanguage("sw");
    expect(await screen.findByText("T 123 ABC imezidi km/h 80 (juu kabisa km/h 95)")).toBeInTheDocument();
  });

  it("bell: unread count, live push, mark all read", async () => {
    signIn(["notifications.view", "alerts.view"]);
    const toastError = vi.spyOn(toast, "error");
    mock.onPost("/notifications/read-all/").reply(200, { updated: 2 });
    renderAt(<AppRoutes />, "/app/alerts");
    expect(await screen.findByTestId("unread-count", {}, { timeout: 4000 })).toHaveTextContent("2");
    await waitFor(() => expect(FakeSocket.last?.url).toContain("/ws/notifications/?ticket=t1"));
    mock.onGet("/notifications/unread/").reply(200, { unread: 3 });
    act(() => FakeSocket.last!.onmessage!({ data: JSON.stringify({ type: "notification", notification: { id: 9, title: "T 9: possible crash", severity: "critical" } }) }));
    expect(toastError).toHaveBeenCalledWith("T 9: possible crash");                             // critical -> error toast
    await waitFor(() => expect(screen.getByTestId("unread-count")).toHaveTextContent("3"));
    await userEvent.click(screen.getByTestId("notification-bell"));
    await userEvent.click(await screen.findByRole("button", { name: "Mark all read" }));
    expect(mock.history.post.some((r) => r.url === "/notifications/read-all/")).toBe(true);
  });

  it("rule form offers the organization's roles (not drivers)", async () => {
    signIn(["alerts.view", "alerts.manage", "vehicles.view"]);
    mock.onGet("/alert-rules/").reply(200, page([]));
    mock.onGet(/\/(vehicles|vehicle-groups|geofences)\//).reply(200, page([]));
    mock.onGet("/roles/").reply(200, page([{ slug: "fleet_manager", name: "Fleet manager", is_system: true },
      { slug: "driver", name: "Driver", is_system: true }]));
    renderAt(<AppRoutes />, "/app/alert-rules");
    await userEvent.click(await screen.findByRole("button", { name: "Add" }, { timeout: 4000 }));
    const roles = within(await screen.findByRole("dialog")).getByLabelText(/Notify roles/);
    await waitFor(() => expect(within(roles).getByRole("option", { name: "Fleet manager" })).toBeInTheDocument());
    expect(within(roles).queryByRole("option", { name: "Driver" })).not.toBeInTheDocument();
    expect(screen.queryByText(/nav\.|vehicles\.groups|roles\.system/)).not.toBeInTheDocument();   // no raw keys
    expect((roles as HTMLSelectElement).multiple).toBe(true);                                     // static multiselect
  });

  it("maps form fields to rule params and back", () => {
    expect(ruleFromForm({ name: "Fast", type: "overspeed", threshold: "100", geofences: [3], require_driver: true, active_from: "", active_to: "" }))
      .toEqual({ name: "Fast", type: "overspeed", params: { limit_kmh: 100 }, schedule: {} });
    expect(ruleFromForm({ type: "geofence_enter", threshold: "", geofences: [3, "4"], active_from: "18:00", active_to: "06:00" }))
      .toEqual({ type: "geofence_enter", params: { geofences: [3, 4] }, schedule: { from: "18:00", to: "06:00" } });
    expect(ruleFromForm({ type: "unauthorized_movement", require_driver: true })).toMatchObject({ params: { require_driver: true } });
    expect(ruleToForm({ id: 1, type: "long_idle", params: { minutes: 20 }, schedule: { from: "06:00", to: "18:00" } }))
      .toMatchObject({ threshold: 20, active_from: "06:00", active_to: "18:00", require_driver: false });
  });
});
