import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MockAdapter from "axios-mock-adapter";
import { api } from "../../services/api";
import { AppRoutes } from "../../app/App";
import { setLanguage } from "../../i18n";
import { renderAt, signIn, signOut } from "../../test-utils";

let mock: MockAdapter;
beforeEach(() => { mock = new MockAdapter(api); setLanguage("en"); });
afterEach(() => { mock.restore(); signOut(); });

const fmt = { id: 3, name: "Acme JSON", slug: "acme", kind: "json", is_active: true, notes: "", models_using: ["Acme HTTP"],
  definition: { kind: "json", identifier: "unit", latitude: "gps.lat", longitude: "gps.lng" } };

describe("tracker formats", () => {
  it("test-parses a sample with the draft definition and shows the records", async () => {
    signIn([], true);
    mock.onGet("/device-catalog/generic-formats/").reply(200, { count: 1, next: null, previous: null, results: [fmt] });
    mock.onPost("/device-catalog/generic-formats/3/test-parse/").reply(200, { ok: true, stage: "sample",
      records: [{ identifier: "ACME-77", latitude: -6.79, longitude: 39.21, sensors: {} }] });
    renderAt(<AppRoutes />, "/app/device-catalog/formats");
    expect(await screen.findByText("POST /acme")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Edit and test" }));
    const dialog = screen.getByRole("dialog");
    expect((within(dialog).getByLabelText("Definition") as HTMLTextAreaElement).value).not.toContain('"kind"');
    await userEvent.type(within(dialog).getByLabelText("Sample message"), '{{"unit": "ACME-77"}');
    await userEvent.click(within(dialog).getByRole("button", { name: "Test parse" }));
    expect(await within(dialog).findByTestId("parse-result")).toHaveTextContent("ACME-77");
    const body = JSON.parse(mock.history.post[0].data as string);
    expect(body.definition.identifier).toBe("unit");
  });

  it("shows the gateway's errors for a sample that doesn't match", async () => {
    signIn([], true);
    mock.onGet("/device-catalog/generic-formats/").reply(200, { count: 1, next: null, previous: null, results: [fmt] });
    mock.onPost("/device-catalog/generic-formats/3/test-parse/").reply(200, { ok: false, stage: "sample", errors: [{ field: "latitude", message: "missing" }] });
    renderAt(<AppRoutes />, "/app/device-catalog/formats");
    await userEvent.click(await screen.findByRole("button", { name: "Edit and test" }));
    const dialog = screen.getByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Sample message"), "x");
    await userEvent.click(within(dialog).getByRole("button", { name: "Test parse" }));
    await waitFor(() => expect(within(dialog).getByRole("alert")).toHaveTextContent("latitude: missing"));
  });

  it("is for Super Administrators only", async () => {
    signIn(["devices.view"]);
    renderAt(<AppRoutes />, "/app/device-catalog/formats");
    expect(await screen.findByText("You don't have access to this page.")).toBeInTheDocument();
  });
});
