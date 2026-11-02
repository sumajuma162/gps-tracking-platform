import { api, refreshSession } from "./api";
import { useAuth } from "../store/auth";

export interface LiveFeed { close: () => void }

/**
 * Subscribe to a server-pushed feed. Each connection gets a fresh one-time ticket (JWTs never go in URLs).
 * Falls back to polling the REST equivalent when WebSockets are unavailable (some proxies block them),
 * so the UI keeps working either way.
 */
export function openLiveFeed<T>(opts: {
  wsPath: string; restPath: string; onData: (data: T) => void; onMode?: (mode: "live" | "polling") => void;
  pollMs?: number;
}): LiveFeed {
  let closed = false;
  let socket: WebSocket | null = null;
  let pollTimer: number | undefined;
  let failures = 0;

  const poll = async () => {
    if (closed) return;
    try { const { data } = await api.get<T>(opts.restPath); opts.onData(data); } catch { /* shown as stale */ }
    pollTimer = window.setTimeout(() => void poll(), opts.pollMs ?? 2000);
  };
  const startPolling = () => { if (pollTimer === undefined) { opts.onMode?.("polling"); void poll(); } };

  const connect = async () => {
    if (closed) return;
    if (typeof WebSocket === "undefined") { startPolling(); return; }
    let ticket: string;
    try { ticket = (await api.post<{ ticket: string }>("/auth/ws-ticket/")).data.ticket; } catch { startPolling(); return; }
    const org = useAuth.getState().activeOrgId;
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${scheme}://${window.location.host}${opts.wsPath}?ticket=${encodeURIComponent(ticket)}${org ? `&org=${org}` : ""}`);
    socket.onopen = () => { failures = 0; opts.onMode?.("live"); };
    socket.onmessage = (ev) => {
      try { const msg = JSON.parse(String(ev.data)) as { data: T }; opts.onData(msg.data); } catch { /* ignore */ }
    };
    socket.onclose = async (ev) => {
      socket = null;
      if (closed) return;
      if (ev.code === 4401) { const ok = await refreshSession(); if (!ok) return; }
      if (ev.code === 4403) { startPolling(); return; }   // REST shows the proper error
      failures += 1;
      if (failures >= 3) startPolling();
      else window.setTimeout(() => void connect(), 1000 * failures);
    };
  };
  void connect();
  return {
    close: () => {
      closed = true;
      socket?.close();
      if (pollTimer !== undefined) window.clearTimeout(pollTimer);
    },
  };
}
