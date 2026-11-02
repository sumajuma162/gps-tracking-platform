import MockAdapter from "axios-mock-adapter";
import { api, errorMessage, refreshSession } from "./api";
import { useAuth } from "../store/auth";
import { signIn } from "../test-utils";

const session = { access: "token-2", user: { id: 1, email: "t@x.test", full_name: "", language: "en", is_platform_admin: false, last_login: null, date_joined: "" }, memberships: [] };

describe("api client", () => {
  let mock: MockAdapter;
  beforeEach(() => { mock = new MockAdapter(api); signIn(["users.view"]); });
  afterEach(() => mock.restore());

  it("sends the access token and active organization", async () => {
    mock.onGet("/roles/").reply((cfg) => [200, { auth: cfg.headers?.Authorization, org: cfg.headers?.["X-Organization-Id"] }]);
    const { data } = await api.get("/roles/");
    expect(data).toEqual({ auth: "Bearer token-1", org: "1" });
  });

  it("refreshes once for concurrent 401s, then retries each request", async () => {
    let refreshCalls = 0;
    mock.onPost("/auth/refresh/").reply(() => { refreshCalls += 1; return [200, session]; });
    mock.onGet("/users/").reply((cfg) => (cfg.headers?.Authorization === "Bearer token-2" ? [200, { ok: 1 }] : [401, {}]));
    mock.onGet("/roles/").reply((cfg) => (cfg.headers?.Authorization === "Bearer token-2" ? [200, { ok: 2 }] : [401, {}]));
    const [a, b] = await Promise.all([api.get("/users/"), api.get("/roles/")]);
    expect([a.data.ok, b.data.ok]).toEqual([1, 2]);
    expect(refreshCalls).toBe(1);
    expect(useAuth.getState().access).toBe("token-2");
  });

  it("signs the user out when refresh fails", async () => {
    mock.onPost("/auth/refresh/").reply(401, {});
    mock.onGet("/users/").reply(401, {});
    await expect(api.get("/users/")).rejects.toBeTruthy();
    expect(useAuth.getState().status).toBe("anonymous");
    expect(await refreshSession()).toBeNull();
  });

  it("never retries the login request itself", async () => {
    let refreshCalls = 0;
    mock.onPost("/auth/refresh/").reply(() => { refreshCalls += 1; return [200, session]; });
    mock.onPost("/auth/login/").reply(401, { error: { code: "login_failed", message: "Email or password is incorrect." } });
    await expect(api.post("/auth/login/", {})).rejects.toBeTruthy();
    expect(refreshCalls).toBe(0);
  });

  it("turns failures into readable messages", async () => {
    mock.onGet("/a/").reply(400, { error: { message: "Server says no." } });
    mock.onGet("/b/").networkError();
    const a = await api.get("/a/").catch((e) => e);
    const b = await api.get("/b/").catch((e) => e);
    expect(errorMessage(a, "fallback", "offline")).toBe("Server says no.");
    expect(errorMessage(b, "fallback", "offline")).toBe("offline");
  });
});
