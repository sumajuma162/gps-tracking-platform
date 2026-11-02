import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MockAdapter from "axios-mock-adapter";
import { api } from "../../services/api";
import { setLanguage } from "../../i18n";
import { renderAt, signIn, signOut } from "../../test-utils";
import { CommandsDialog } from "./CommandsDialog";

let mock: MockAdapter;
const page = <T,>(results: T[]) => ({ count: results.length, next: null, previous: null, results });
beforeEach(() => {
  mock = new MockAdapter(api); setLanguage("en");
  mock.onGet("/device-catalog/command-templates/").reply(200, page([
    { id: 1, name: "Device information", command_text: "getinfo", description: "", is_dangerous: false, is_verified: false },
    { id: 2, name: "Immobilize", command_text: "setdigout 1", description: "", is_dangerous: true, is_verified: true }]));
  mock.onGet("/commands/").reply(200, page([{ id: 9, name: "Device information", command_text: "getinfo", status: "ACKNOWLEDGED",
    created_at: "2026-10-01T06:00:00Z", error: "", requested_by_email: "a@b", responses: [{ response_text: "RTC:2026/10/01 06:00 Init:...", received_at: "2026-10-01T06:00:01Z" }] }]));
  mock.onPost("/commands/").reply(201, { id: 10 });
});
afterEach(() => { mock.restore(); signOut(); });

it("sends read-only commands at once, output control only after confirmation, and shows responses", async () => {
  signIn(["devices.view", "commands.send"]);
  renderAt(<CommandsDialog device={{ id: 3, name: "Truck 1", device_model: 4 }} onClose={() => undefined} />, "/");
  expect(await screen.findByTestId("command-response", {}, { timeout: 4000 })).toHaveTextContent("RTC:2026/10/01");
  const list = screen.getByRole("list", { name: "Available commands" });
  expect(within(list).getByText("Not verified on hardware")).toBeInTheDocument();
  await userEvent.click(within(list).getAllByRole("button", { name: "Send" })[0]);
  await waitFor(() => expect(JSON.parse(mock.history.post[0].data)).toEqual({ device: 3, template: 1, confirm: false }));
  await userEvent.click(within(list).getAllByRole("button", { name: "Send" })[1]);
  expect(mock.history.post).toHaveLength(1);                                  // nothing sent before confirming
  await userEvent.click(await screen.findByRole("button", { name: "Yes, send it" }));
  await waitFor(() => expect(JSON.parse(mock.history.post[1].data)).toEqual({ device: 3, template: 2, confirm: true }));
});
