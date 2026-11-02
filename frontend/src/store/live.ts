import { create } from "zustand";

export interface LiveVehicle {
  id: number; device_id: number; device_name: string; identifier: string; model: string;
  vehicle: null | { id: number; registration_number: string; make: string; model: string; vehicle_type: string; groups: number[] };
  driver: null | { id: number; name: string; phone: string };
  status: string; latitude: number | null; longitude: number | null; speed_kmh: number | null; heading: number | null;
  satellites: number | null; ignition: boolean | null; gps_valid: boolean; fix_time: string | null; last_packet_at: string | null;
  external_voltage: number | null; battery_voltage: number | null; gsm_signal: number | null;
}
export type LiveUpdate = Partial<LiveVehicle> & { device_id: number };
export type Connection = "connecting" | "live" | "reconnecting" | "polling";

interface LiveState {
  vehicles: Record<number, LiveVehicle>;
  connection: Connection;
  lastUpdateAt: number | null;
  /** An update arrived for a tracker the snapshot did not contain: the page should refetch the snapshot. */
  needsSnapshot: boolean;
  setSnapshot: (rows: LiveVehicle[]) => void;
  applyUpdates: (updates: LiveUpdate[]) => void;
  setConnection: (c: Connection) => void;
}

const DYNAMIC: (keyof LiveVehicle)[] = ["status", "latitude", "longitude", "speed_kmh", "heading", "satellites", "ignition",
  "gps_valid", "fix_time", "last_packet_at", "external_voltage", "battery_voltage", "gsm_signal"];

export const useLive = create<LiveState>((set) => ({
  vehicles: {},
  connection: "connecting",
  lastUpdateAt: null,
  needsSnapshot: false,
  setSnapshot: (rows) => set({ vehicles: Object.fromEntries(rows.map((r) => [r.device_id, r])), needsSnapshot: false }),
  applyUpdates: (updates) => set((state) => {
    const vehicles = { ...state.vehicles };
    let unknown = false;
    for (const u of updates) {
      const current = vehicles[u.device_id];
      if (!current) { unknown = true; continue; }
      const next = { ...current };
      for (const key of DYNAMIC) if (key in u) (next as Record<string, unknown>)[key] = u[key];
      vehicles[u.device_id] = next;
    }
    return { vehicles, lastUpdateAt: Date.now(), needsSnapshot: state.needsSnapshot || unknown };
  }),
  setConnection: (connection) => set({ connection }),
}));

export const STATUS_ORDER = ["ONLINE_MOVING", "ONLINE_IDLE", "ONLINE_STOPPED", "NO_GPS", "OFFLINE", "UNKNOWN"] as const;

export function vehicleLabel(v: LiveVehicle): string {
  return v.vehicle?.registration_number ?? v.device_name;
}
