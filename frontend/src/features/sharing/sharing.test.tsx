import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axios from "axios";
import MockAdapter from "axios-mock-adapter";
import { Route, Routes } from "react-router-dom";
import { api } from "../../services/api";
import { setLanguage } from "../../i18n";
import { renderAt, signIn, signOut } from "../../test-utils";
import { SharePage } from "./SharePage";
import { GlobalSearch } from "../system/GlobalSearch";
import { SystemHealthPage } from "../system/SystemHealthPage";

const markers: unknown[] = [];
vi.mock("../../maps", () => ({
  createMap: () => ({ setMarkers: (m: unknown[]) => { markers.push(...m); }, panTo: () => undefined, fitTo: () => undefined,
    setRoute: () => undefined, destroy: () => undefined }),
}));

let pub: MockAdapter;
let mock: MockAdapter;
beforeEach(() => { pub = new MockAdapter(axios); mock = new MockAdapter(api); setLanguage("en"); markers.length = 0; });
afterEach(() => { pub.restore(); mock.restore(); signOut(); });

it("public share link shows the vehicle's position, and an expired link says so", async () => {
  pub.onGet("/api/v1/public/share/abc/").reply(200, { vehicle: "T 123 ABC", label: "", expires_at: "2026-10-04T10:00:00Z",
    position: { lat: -6.8, lon: 39.28, heading: 90, speed_kmh: 40, status: "ONLINE_MOVING", updated_at: "2026-10-03T10:00:00Z" } });
  renderAt(<Routes><Route path="/share/:token" element={<SharePage />} /></Routes>, "/share/abc");
  expect(await screen.findByRole("heading", { name: "T 123 ABC" }, { timeout: 4000 })).toBeInTheDocument();
  expect(screen.getByTestId("share-meta")).toHaveTextContent("Link valid until");
  await waitFor(() => expect(markers).toHaveLength(1));
  pub.onGet("/api/v1/public/share/gone/").reply(404);
  renderAt(<Routes><Route path="/share/:token" element={<SharePage />} /></Routes>, "/share/gone");
  expect(await screen.findByText("This link has expired or was revoked.")).toBeInTheDocument();
});

it("global search lists results by kind", async () => {
  signIn(["vehicles.view"]);
  mock.onGet("/search/").reply(200, { results: { vehicles: [{ id: 7, label: "T 777 FND", detail: "Isuzu" }] } });
  renderAt(<GlobalSearch />, "/app");
  await userEvent.type(screen.getByTestId("global-search"), "777");
  expect(await screen.findByRole("option", { name: /T 777 FND/ }, { timeout: 3000 })).toBeInTheDocument();
  expect(mock.history.get[0].params).toEqual({ q: "777" });
});

it("system health shows each check and the overall status", async () => {
  signIn([], true);
  mock.onGet("/system/health/").reply(200, { status: "degraded", down: ["processor"], checked_at: "2026-10-03T10:00:00Z",
    checks: { database: { status: "ok", latency_ms: 1.2 }, processor: { status: "down", error: "URLError" } } });
  renderAt(<SystemHealthPage />, "/app/system");
  expect(await screen.findByText("Degraded", {}, { timeout: 4000 })).toBeInTheDocument();
  expect(document.querySelector('[data-check="processor"]')).toHaveTextContent("down");
});
