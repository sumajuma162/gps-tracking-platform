import { api, refreshSession } from "./api";
import { useAuth } from "../store/auth";
import { useLive, type LiveUpdate, type LiveVehicle } from "../store/live";
import type { Bounds } from "../maps/types";

/**
 * Keeps the live store in sync: REST snapshot first, then WebSocket updates (batched by the server, <= 1/s).
 * After every reconnect the snapshot is fetched again so nothing missed while offline stays stale (spec A12).
 * Falls back to polling the snapshot every 10 s when WebSockets keep failing.
 */
export function startTracking(options: { retryBaseMs?: number; pollMs?: number } = {}): { close: () => void; setViewport: (b: Bounds | null) => void } {
  const retryBase = options.retryBaseMs ?? 1000;
  const pollMs = options.pollMs ?? 10_000;
  const store = useLive.getState();
  let closed = false;
  let socket: WebSocket | null = null;
  let failures = 0;
  let pollTimer: number | undefined;
  let viewport: Bounds | null = null;

  const snapshot = async () => {
    try { store.setSnapshot((await api.get<LiveVehicle[]>("/tracking/live/")).data); } catch { /* keep last known */ }
  };
  const poll = async () => {
    if (closed) return;
    await snapshot();
    pollTimer = window.setTimeout(() => void poll(), pollMs);
  };
  const startPolling = () => { if (pollTimer === undefined) { useLive.getState().setConnection("polling"); void poll(); } };

  const connect = async () => {
    if (closed) return;
    if (typeof WebSocket === "undefined") { startPolling(); return; }
    let ticket: string;
    try { ticket = (await api.post<{ ticket: string }>("/auth/ws-ticket/")).data.ticket; } catch { startPolling(); return; }
    const org = useAuth.getState().activeOrgId;
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${scheme}://${window.location.host}/ws/tracking/?ticket=${encodeURIComponent(ticket)}${org ? `&org=${org}` : ""}`);
    socket.onopen = () => {
      failures = 0;
      useLive.getState().setConnection("live");
      if (viewport) socket?.send(JSON.stringify({ type: "viewport", bounds: viewport }));
      void snapshot();   // resynchronise: anything that changed while we were away
    };
    socket.onmessage = (ev) => {
      try {
        const msg = JSON.parse(String(ev.data)) as { type: string; updates?: LiveUpdate[] };
        if (msg.type === "updates" && msg.updates) {
          useLive.getState().applyUpdates(msg.updates);
          if (useLive.getState().needsSnapshot) void snapshot();
        }
      } catch { /* ignore malformed */ }
    };
    socket.onclose = async (ev) => {
      socket = null;
      if (closed) return;
      useLive.getState().setConnection("reconnecting");
      if (ev.code === 4401 && !(await refreshSession())) return;
      if (ev.code === 4403) { startPolling(); return; }
      failures += 1;
      if (failures >= 4) startPolling();
      else window.setTimeout(() => void connect(), Math.min(10 * retryBase, retryBase * 2 ** failures));
    };
  };

  void snapshot().then(() => connect());
  return {
    close: () => { closed = true; socket?.close(); if (pollTimer !== undefined) window.clearTimeout(pollTimer); },
    setViewport: (b) => {
      viewport = b;
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "viewport", bounds: b }));
    },
  };
}
