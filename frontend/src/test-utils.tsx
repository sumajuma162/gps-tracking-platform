import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { makeQueryClient } from "./app/App";
import { useAuth, type Membership } from "./store/auth";

export function renderAt(ui: ReactElement, path = "/") {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

export function membership(perms: string[], org = { id: 1, name: "Demo Logistics Ltd" }): Membership {
  return { organization: { ...org, slug: "demo", timezone: "Africa/Dar_es_Salaam", language: "en", distance_unit: "km" },
    role: { id: 1, slug: "x", name: "Tester" }, permissions: perms };
}

export function signIn(perms: string[], isPlatformAdmin = false) {
  useAuth.setState({ status: "authenticated", access: "token-1", activeOrgId: 1,
    user: { id: 1, email: "t@x.test", full_name: "Test User", language: "en", is_platform_admin: isPlatformAdmin, last_login: null, date_joined: "" },
    memberships: [membership(perms)] });
}

export function signOut() {
  useAuth.setState({ status: "anonymous", access: null, user: null, memberships: [], activeOrgId: null });
}
