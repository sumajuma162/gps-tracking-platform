import { create } from "zustand";

export interface User {
  id: number; email: string; full_name: string; language: "en" | "sw"; is_platform_admin: boolean;
  last_login: string | null; date_joined: string;
}
export interface Membership {
  organization: { id: number; name: string; slug: string; timezone: string; language: string; distance_unit: string };
  role: { id: number; slug: string; name: string };
  permissions: string[];
}
export interface SessionPayload { access?: string; user: User; memberships: Membership[] }

type Status = "unknown" | "authenticated" | "anonymous";
const ORG_KEY = "gps.activeOrg";

interface AuthState {
  status: Status;
  access: string | null;
  user: User | null;
  memberships: Membership[];
  activeOrgId: number | null;
  setSession: (payload: SessionPayload) => void;
  setAccess: (token: string) => void;
  setActiveOrg: (id: number) => void;
  clear: () => void;
}

function storedOrg(): number | null {
  try { const v = Number(localStorage.getItem(ORG_KEY)); return Number.isFinite(v) && v > 0 ? v : null; } catch { return null; }
}

export const useAuth = create<AuthState>((set, get) => ({
  status: "unknown",
  access: null,
  user: null,
  memberships: [],
  activeOrgId: storedOrg(),
  setSession: ({ access, user, memberships }) => {
    const current = get().activeOrgId;
    const valid = memberships.some((m) => m.organization.id === current);
    const activeOrgId = valid ? current : memberships[0]?.organization.id ?? null;
    set({ status: "authenticated", access: access ?? get().access, user, memberships, activeOrgId });
  },
  setAccess: (token) => set({ access: token }),
  setActiveOrg: (id) => {
    try { localStorage.setItem(ORG_KEY, String(id)); } catch { /* ignore */ }
    set({ activeOrgId: id });
  },
  clear: () => set({ status: "anonymous", access: null, user: null, memberships: [] }),
}));

export function activeMembership(s: Pick<AuthState, "memberships" | "activeOrgId">): Membership | null {
  return s.memberships.find((m) => m.organization.id === s.activeOrgId) ?? null;
}

export function hasPermission(s: Pick<AuthState, "memberships" | "activeOrgId">, perm: string): boolean {
  return activeMembership(s)?.permissions.includes(perm) ?? false;
}
