import { screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MockAdapter from "axios-mock-adapter";
import { Route, Routes } from "react-router-dom";
import { api } from "../services/api";
import { AppRoutes } from "../app/App";
import { safeNext } from "../features/auth/RequireAuth";
import { setLanguage } from "../i18n";
import { renderAt, signIn, signOut } from "../test-utils";

let mock: MockAdapter;
beforeEach(() => { mock = new MockAdapter(api); setLanguage("en"); });
afterEach(() => { mock.restore(); signOut(); });

describe("landing page", () => {
  it("is public, links to sign in and makes no API calls", () => {
    signOut();
    renderAt(<AppRoutes />, "/");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Know where every vehicle is");
    expect(screen.getByRole("link", { name: "Sign in to your fleet" })).toHaveAttribute("href", "/login");
    expect(screen.getByRole("img", { name: /Not live data/ })).toBeInTheDocument();
    expect(mock.history.get.length + mock.history.post.length).toBe(0);
  });
});

describe("sign in", () => {
  it("validates fields before calling the server", async () => {
    signOut();
    renderAt(<AppRoutes />, "/login");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Enter a valid email address.")).toBeInTheDocument();
    expect(screen.getByText("Enter your password.")).toBeInTheDocument();
    expect(mock.history.post.length).toBe(0);
  });

  it("shows the server's message when sign-in fails", async () => {
    signOut();
    mock.onPost("/auth/login/").reply(401, { error: { code: "login_failed", message: "Email or password is incorrect." } });
    renderAt(<AppRoutes />, "/login");
    await userEvent.type(screen.getByLabelText("Email"), "a@b.co");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect.");
  });

  it("signs in and returns to the requested page", async () => {
    signOut();
    mock.onPost("/auth/login/").reply(200, { access: "tok", user: { id: 1, email: "a@b.co", full_name: "Asha", language: "en", is_platform_admin: false, last_login: null, date_joined: "" },
      memberships: [{ organization: { id: 7, name: "Demo Logistics Ltd", slug: "d", timezone: "Africa/Dar_es_Salaam", language: "en", distance_unit: "km" }, role: { id: 1, slug: "viewer", name: "Viewer" }, permissions: [] }] });
    renderAt(<Routes><Route path="*" element={<AppRoutes />} /></Routes>, "/login?next=%2Fapp%2Fprofile");
    await userEvent.type(screen.getByLabelText("Email"), "a@b.co");
    await userEvent.type(screen.getByLabelText("Password"), "Correct-Horse-9");
    mock.onGet("/auth/sessions/").reply(200, []);
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Profile" })).toBeInTheDocument();
  });

  it("only follows same-site relative return paths", () => {
    expect(safeNext("/app/users")).toBe("/app/users");
    expect(safeNext("https://evil.example")).toBe("/app");
    for (const bad of ["//evil.example", "/\\evil.example", "/\t/evil.example", "/\n/evil.example", "/x\\y", "/javascript:alert(1)"]) {
      expect(safeNext(bad)).toBe("/app");
    }
    expect(safeNext("/app/vehicles?q=T%20123#top")).toBe("/app/vehicles?q=T%20123#top");
    expect(safeNext("//evil.example")).toBe("/app");
    expect(safeNext("/\\evil.example")).toBe("/app");
    expect(safeNext(null)).toBe("/app");
  });
});

describe("protected app", () => {
  it("sends anonymous visitors to sign in, keeping the destination", async () => {
    signOut();
    renderAt(<AppRoutes />, "/app/users");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("restores a session from the refresh cookie on load", async () => {
    signOut();
    const { useAuth } = await import("../store/auth");
    useAuth.setState({ status: "unknown" });
    mock.onPost("/auth/refresh/").reply(200, { access: "tok", user: { id: 1, email: "a@b.co", full_name: "Asha", language: "en", is_platform_admin: false, last_login: null, date_joined: "" }, memberships: [] });
    renderAt(<AppRoutes />, "/app/profile");
    mock.onGet("/auth/sessions/").reply(200, []);
    expect(await screen.findByRole("heading", { name: "Profile" })).toBeInTheDocument();
  });

  it("hides pages the role cannot use, and the menu matches", async () => {
    signIn(["dashboard.view"]);
    renderAt(<AppRoutes />, "/app/users");
    expect(await screen.findByText("You don't have access to this page.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
  });

  it("lists users for administrators", async () => {
    signIn(["users.view", "users.manage"]);
    mock.onGet("/users/").reply(200, { count: 1, next: null, previous: null, results: [
      { id: 5, is_active: true, created_at: "", user: { id: 2, email: "fleet@demo.local", full_name: "Baraka Mushi", last_login: null, is_locked: true }, role: { id: 2, slug: "fleet_manager", name: "Fleet manager" } }] });
    mock.onGet("/roles/").reply(200, { count: 1, next: null, previous: null, results: [{ id: 2, slug: "fleet_manager", name: "Fleet manager", is_system: true, permission_codes: [] }] });
    renderAt(<AppRoutes />, "/app/users");
    // Rendered twice: mobile card list and desktop table (CSS decides which is visible).
    expect((await screen.findAllByText("Baraka Mushi")).length).toBe(2);
    expect(screen.getAllByText("Locked").length).toBe(2);
    expect(screen.getByRole("button", { name: "Add user" })).toBeInTheDocument();
  });

  it("shows a retryable error instead of a stack trace when the API fails", async () => {
    signIn(["audit.view"]);
    mock.onGet("/audit-logs/").networkError();
    renderAt(<AppRoutes />, "/app/audit");
    expect(await screen.findByText("Can't reach the server. Check your connection and try again.", {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("switches the interface to Kiswahili", async () => {
    signIn(["users.view", "audit.view"]);
    mock.onGet("/users/").reply(200, { count: 0, next: null, previous: null, results: [] });
    renderAt(<AppRoutes />, "/app");
    expect(screen.getByRole("link", { name: "Users" })).toBeInTheDocument();
    act(() => setLanguage("sw"));
    await waitFor(() => expect(screen.getByRole("link", { name: "Watumiaji" })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Toka/ })).toBeInTheDocument();
  });
});

describe("role changes", () => {
  it("asks for confirmation before changing a role", async () => {
    signIn(["users.view", "users.manage"]);
    mock.onGet("/users/").reply(200, { count: 1, next: null, previous: null, results: [
      { id: 5, is_active: true, created_at: "", user: { id: 2, email: "fleet@demo.local", full_name: "Baraka Mushi", last_login: null, is_locked: false }, role: { id: 2, slug: "fleet_manager", name: "Fleet manager" } }] });
    mock.onGet("/roles/").reply(200, { count: 2, next: null, previous: null, results: [
      { id: 2, slug: "fleet_manager", name: "Fleet manager", is_system: true, permission_codes: [] },
      { id: 3, slug: "viewer", name: "Viewer", is_system: true, permission_codes: [] }] });
    mock.onPatch("/users/5/").reply(200, {});
    renderAt(<AppRoutes />, "/app/users");
    const selects = await screen.findAllByLabelText("Change role");
    await userEvent.selectOptions(selects[0], "3");
    expect(mock.history.patch.length).toBe(0);
    expect(await screen.findByText("Change fleet@demo.local from Fleet manager to Viewer?")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Change role" }));
    await waitFor(() => expect(mock.history.patch.length).toBe(1));
    expect(JSON.parse(mock.history.patch[0].data)).toEqual({ role_id: 3 });
  });
});
