import axios, { AxiosError, type AxiosRequestConfig, type InternalAxiosRequestConfig } from "axios";
import { useAuth, type SessionPayload } from "../store/auth";

export const api = axios.create({ baseURL: "/api/v1", withCredentials: true, timeout: 20_000 });

export function readCookie(name: string): string | null {
  const match = document.cookie.split("; ").find((c) => c.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : null;
}

const UNSAFE = new Set(["post", "put", "patch", "delete"]);
const NO_RETRY = ["/auth/login/", "/auth/refresh/", "/auth/logout/", "/auth/password/"];

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const { access, activeOrgId } = useAuth.getState();
  if (access) config.headers.set("Authorization", `Bearer ${access}`);
  if (activeOrgId) config.headers.set("X-Organization-Id", String(activeOrgId));
  if (UNSAFE.has((config.method ?? "get").toLowerCase())) {
    const csrf = readCookie("csrftoken");
    if (csrf) config.headers.set("X-CSRFToken", csrf);
  }
  return config;
});

let refreshing: Promise<string | null> | null = null;

/** Exchange the httpOnly refresh cookie for a new access token. Concurrent callers share one request. */
export function refreshSession(): Promise<string | null> {
  refreshing ??= api
    .post<SessionPayload & { access: string }>("/auth/refresh/")
    .then(({ data }) => {
      useAuth.getState().setSession(data);
      return data.access;
    })
    .catch(() => {
      useAuth.getState().clear();
      return null;
    })
    .finally(() => { refreshing = null; });
  return refreshing;
}

api.interceptors.response.use(undefined, async (error: AxiosError) => {
  const original = error.config as (AxiosRequestConfig & { _retried?: boolean }) | undefined;
  const url = original?.url ?? "";
  if (error.response?.status === 401 && original && !original._retried && !NO_RETRY.some((p) => url.includes(p))) {
    original._retried = true;
    const token = await refreshSession();
    if (token) return api(original);
  }
  return Promise.reject(error);
});

export interface ApiErrorBody { error?: { code?: string; message?: string; details?: Record<string, string[] | string> } }

/** A user-facing message for any API failure: the server's message, or a plain network/generic one. */
export function errorMessage(err: unknown, fallback: string, network: string): string {
  if (axios.isAxiosError(err)) {
    if (!err.response) return network;
    const body = err.response.data as ApiErrorBody | undefined;
    if (body?.error?.message) return body.error.message;
  }
  return fallback;
}

export function fieldErrors(err: unknown): Record<string, string> {
  if (!axios.isAxiosError(err)) return {};
  const details = (err.response?.data as ApiErrorBody | undefined)?.error?.details ?? {};
  return Object.fromEntries(Object.entries(details).map(([k, v]) => [k, Array.isArray(v) ? v.join(" ") : String(v)]));
}
