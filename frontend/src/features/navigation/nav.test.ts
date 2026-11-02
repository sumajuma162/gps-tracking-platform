import { visibleNav } from "./nav";
import { membership } from "../../test-utils";

const keys = (perms: string[], isPlatformAdmin = false) =>
  visibleNav({ isPlatformAdmin, memberships: [membership(perms)], activeOrgId: 1 }).map((i) => i.key);

describe("role-filtered navigation", () => {
  it("shows a viewer only the basics", () => {
    expect(keys(["dashboard.view", "tracking.view_live"])).toEqual(["overview", "liveMap", "profile"]);
  });
  it("shows administrators user management and the audit log", () => {
    expect(keys(["users.view", "users.manage", "audit.view"])).toEqual(["overview", "users", "roles", "audit", "profile"]);
  });
  it("shows Organizations only to platform Super Administrators", () => {
    expect(keys([], true)).toContain("organizations");
    expect(keys(["users.view", "audit.view"])).not.toContain("organizations");
    expect(keys(["devices.manage"])).toContain("manualPosition");
    expect(keys(["devices.view"])).not.toContain("manualPosition");
  });
  it("platform admins without a membership still get organizations and profile", () => {
    expect(visibleNav({ isPlatformAdmin: true, memberships: [], activeOrgId: null }).map((i) => i.key)).toEqual([
      "settings", "organizations", "catalog", "system", "ports", "formats", "unregistered", "manualPosition", "profile"]);   // settings: platform defaults
  });
});

describe("fleet navigation", () => {
  it("follows each module's view permission", () => {
    expect(keys(["vehicles.view", "devices.view"])).toEqual(["overview", "vehicles", "devices", "profile"]);
    expect(keys(["drivers.view", "sims.view"])).toEqual(["overview", "drivers", "sims", "profile"]);
  });
});
