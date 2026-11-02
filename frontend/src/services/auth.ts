import { api } from "./api";
import { useAuth, type SessionPayload } from "../store/auth";

export async function login(email: string, password: string): Promise<void> {
  const { data } = await api.post<SessionPayload>("/auth/login/", { email, password });
  useAuth.getState().setSession(data);
}

export async function logout(): Promise<void> {
  try { await api.post("/auth/logout/"); } finally { useAuth.getState().clear(); }
}
