import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, CheckCircle2, Circle, Loader2, Radio } from "lucide-react";
import { openLiveFeed } from "../../services/ws";
import { Badge, ErrorState, SkeletonRows, formatDateTime } from "../../components/ui";

export interface Diagnostics {
  device: { id: number; name: string; identifier: string; is_active: boolean; model: string };
  stages: { key: string; done: boolean }[];
  current_stage: string | null;
  connection: null | { connected_at: string; disconnected_at: string | null; peer: string; transport: string; port: number;
    packets: number; records: number; close_reason: string };
  latest: null | { device_time: string; latitude: number; longitude: number; speed_kmh: number | null; satellites: number | null;
    gps_valid: boolean; mapped: Record<string, number | boolean>; sensors: Record<string, number>; events: string[];
    raw_io: Record<string, number | string>; lag_seconds: number };
  last_packet_at: string | null;
  possible_typos: { identifier: string; last_seen_at: string; attempts: number }[];
  raw_packets?: { received_at: string; direction: string; transport: string; decode_result: string; payload_hex: string }[];
}

const UNITS: Record<string, string> = { external_voltage: "V", battery_voltage: "V", odometer_km: "km", gsm_signal: "/5" };

function formatValue(key: string, v: number | boolean, yes: string, no: string): string {
  if (typeof v === "boolean") return v ? yes : no;
  const unit = UNITS[key];
  const text = key === "odometer_km" ? v.toLocaleString(undefined, { maximumFractionDigits: 1 }) : String(Math.round(v * 1000) / 1000);
  return unit ? `${text} ${unit}` : text;
}

const REASONS = ["device_closed", "timeout", "connection_lost", "device_disabled", "unknown_device", "storage_unavailable"];

/** Plain-language reason a connection ended (gateway codes such as "protocol:bad_crc" -> "invalid data"). */
function closeReason(code: string, t: (k: string) => string): string {
  if (!code) return "—";
  if (code.startsWith("protocol:")) return t("diag.reason.protocol");
  return REASONS.includes(code) ? t(`diag.reason.${code}`) : code;
}

export function DiagnosticsPanel({ deviceId }: { deviceId: number }) {
  const { t } = useTranslation();
  const [data, setData] = useState<Diagnostics | null>(null);
  const [mode, setMode] = useState<"live" | "polling" | null>(null);
  const [error, setError] = useState(false);
  const received = useRef(false);

  useEffect(() => {
    received.current = false;
    setError(false);
    const timer = window.setTimeout(() => { if (!received.current) setError(true); }, 15000);
    const feed = openLiveFeed<Diagnostics>({
      wsPath: `/ws/devices/${deviceId}/diagnostics/`, restPath: `/devices/${deviceId}/diagnostics/`,
      onData: (d) => { received.current = true; setData(d); setError(false); }, onMode: setMode,
    });
    return () => { feed.close(); window.clearTimeout(timer); };
  }, [deviceId]);

  if (!data) return error ? <ErrorState message={t("errors.network")} /> : <SkeletonRows rows={5} />;
  const current = data.current_stage;
  return (
    <div className="space-y-4" data-testid="diagnostics">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-bold">{t("diag.title")}</h3>
        <span className="flex items-center gap-1 text-xs text-ink-soft">
          <Radio className={`h-3.5 w-3.5 ${mode === "live" ? "text-moving" : ""}`} aria-hidden />
          {mode === "live" ? t("diag.live") : t("diag.polling")}
        </span>
      </div>
      <ol className="space-y-2" aria-label={t("diag.title")}>
        {data.stages.map((s) => {
          const active = s.key === current;
          return (
            <li key={s.key} className={`flex items-start gap-3 rounded-md border px-3 py-2 ${active ? "border-marking bg-marking-soft" : "border-line"}`}
              data-stage={s.key} data-done={s.done}>
              {s.done ? <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-moving" aria-label={t("diag.done")} />
                : active ? <Loader2 className="mt-0.5 h-5 w-5 shrink-0 animate-spin text-idle" aria-label={t("diag.waiting")} />
                  : <Circle className="mt-0.5 h-5 w-5 shrink-0 text-offline" aria-label={t("diag.notYet")} />}
              <div>
                <div className="font-semibold">{t(`diag.stage.${s.key}`)}</div>
                {active && <p className="text-sm text-ink-soft">{t(`diag.help.${s.key}`)}</p>}
              </div>
            </li>
          );
        })}
      </ol>
      {current === null && <p role="status" className="rounded-md bg-moving/10 p-3 font-medium text-moving">{t("diag.allDone")}</p>}
      {!data.device.is_active && <p className="rounded-md bg-critical/10 p-3 text-critical">{t("diag.disabled")}</p>}
      {data.possible_typos.length > 0 && (
        <div role="alert" className="flex gap-2 rounded-md border border-idle/40 bg-idle/10 p-3 text-sm">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-idle" aria-hidden />
          <div>
            <p className="font-semibold">{t("diag.typoTitle")}</p>
            <p>{t("diag.typoBody", { registered: data.device.identifier })}</p>
            <ul className="mt-1 font-mono">{data.possible_typos.map((p) => <li key={p.identifier}>{p.identifier} · {formatDateTime(p.last_seen_at)}</li>)}</ul>
          </div>
        </div>
      )}
      {data.connection && (
        <dl className="grid grid-cols-2 gap-2 rounded-md bg-paper p-3 text-sm sm:grid-cols-4">
          <div><dt className="text-ink-soft">{t("diag.from")}</dt><dd className="break-all font-medium">{data.connection.peer}</dd></div>
          <div><dt className="text-ink-soft">{t("diag.connectedAt")}</dt><dd className="font-medium">{formatDateTime(data.connection.connected_at)}</dd></div>
          <div><dt className="text-ink-soft">{t("diag.packets")}</dt><dd className="font-medium">{data.connection.packets} / {data.connection.records}</dd></div>
          <div><dt className="text-ink-soft">{t("common.status")}</dt><dd>{data.connection.disconnected_at
            ? <Badge>{t("diag.closed", { reason: closeReason(data.connection.close_reason, t) })}</Badge> : <Badge tone="good">{t("diag.open")}</Badge>}</dd></div>
        </dl>
      )}
      {data.latest && (
        <div>
          <h4 className="mb-2 text-sm font-semibold">{t("diag.latest", { time: formatDateTime(data.latest.device_time) })}</h4>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-3">
            <div><dt className="text-ink-soft">{t("diag.position")}</dt><dd className="font-mono" data-testid="diag-position">{data.latest.gps_valid
              ? `${data.latest.latitude.toFixed(5)}, ${data.latest.longitude.toFixed(5)}` : t("diag.noFix")}</dd></div>
            <div><dt className="text-ink-soft">{t("diag.speed")}</dt><dd>{data.latest.speed_kmh ?? "—"} km/h</dd></div>
            <div><dt className="text-ink-soft">{t("diag.satellites")}</dt><dd>{data.latest.satellites ?? "—"}</dd></div>
            {Object.entries(data.latest.mapped).map(([k, v]) => (
              <div key={k}><dt className="text-ink-soft">{t(`diag.field.${k}`, { defaultValue: k })}</dt>
                <dd data-testid={`diag-${k}`}>{formatValue(k, v, t("diag.on"), t("diag.off"))}</dd></div>))}
            {Object.entries(data.latest.sensors).map(([k, v]) => (
              <div key={k}><dt className="text-ink-soft">{k}</dt><dd>{String(v)}</dd></div>))}
          </dl>
        </div>
      )}
      {data.raw_packets && data.raw_packets.length > 0 && (
        <details className="rounded-md border border-line">
          <summary className="cursor-pointer px-3 py-2 text-sm font-semibold">{t("diag.rawPackets")}</summary>
          <ul className="max-h-48 divide-y divide-line overflow-y-auto text-xs">
            {data.raw_packets.map((p, i) => (
              <li key={i} className="px-3 py-1.5"><span className="text-ink-soft">{formatDateTime(p.received_at)} · {p.direction} · {p.decode_result}</span>
                <code className="block break-all">{p.payload_hex.slice(0, 160)}{p.payload_hex.length > 160 ? "…" : ""}</code></li>))}
          </ul>
        </details>
      )}
    </div>
  );
}
