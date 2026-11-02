import { useEffect, useMemo, useRef } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApiQuery } from "../../services/hooks";
import { startTracking } from "../../services/tracking";
import { STATUS_ORDER, useLive, vehicleLabel } from "../../store/live";
import { activeMembership, hasPermission, useAuth } from "../../store/auth";
import { createMap } from "../../maps";
import type { MapAdapter } from "../../maps/types";
import { STATUS_COLORS } from "../../maps/types";
import { PageHeader, SkeletonRows } from "../../components/ui";
import { RelativeTime } from "./RelativeTime";
import { DashboardAlerts } from "../alerts/DashboardAlerts";
import { StatusDot } from "./LiveMapPage";

interface Summary { vehicles: number; vehicles_without_tracker: number; devices: number; online_devices: number;
  by_status: Record<string, number>; health: Record<string, { device_id: number; name: string; last_packet_at: string | null }[]>;
  trips_today?: number; distance_today_km?: number }
interface MapSettings { tile_url: string; attribution: string; center: [number, number]; zoom: number }

function Card({ label, value, status, testId }: { label: string; value: number | string; status?: string; testId: string }) {
  return (
    <div className="rounded-lg border border-line bg-panel p-4" data-testid={testId}>
      <div className="flex items-center gap-2 text-sm text-ink-soft">{status && <StatusDot status={status} />}{label}</div>
      <div className="mt-1 font-display text-3xl font-bold">{value}</div>
    </div>
  );
}

export function DashboardPage() {
  const { t } = useTranslation();
  const auth = useAuth();
  const membership = activeMembership(auth);
  const canLive = hasPermission(auth, "tracking.view_live");
  const summary = useApiQuery<Summary>("/dashboard/summary/", undefined, { refetchInterval: 60_000 });
  const settings = useApiQuery<MapSettings>("/settings/map/", undefined, { enabled: canLive });
  const vehicles = useLive((s) => s.vehicles);
  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<MapAdapter | null>(null);

  useEffect(() => { if (!canLive) return; const h = startTracking(); return () => h.close(); }, [canLive]);
  const tracked = useMemo(() => Object.values(vehicles).filter((v) => v.vehicle), [vehicles]);
  const live = canLive && tracked.length > 0;
  const byStatus = useMemo(() => {
    if (!live) return summary.data?.by_status ?? {};
    const c: Record<string, number> = {};
    for (const v of tracked) c[v.status] = (c[v.status] ?? 0) + 1;
    return c;
  }, [live, tracked, summary.data]);
  const online = live ? Object.values(vehicles).filter((v) => !["OFFLINE", "UNKNOWN"].includes(v.status)).length : summary.data?.online_devices ?? 0;

  useEffect(() => {
    if (!mapEl.current || !settings.data || map.current) return;
    map.current = createMap(mapEl.current, { tileUrl: settings.data.tile_url, attribution: settings.data.attribution,
      center: settings.data.center, zoom: settings.data.zoom, cluster: true, interactive: false });
    return () => { map.current?.destroy(); map.current = null; };
  }, [settings.data]);
  useEffect(() => {
    const located = tracked.filter((v) => v.latitude !== null && v.longitude !== null);
    map.current?.setMarkers(located.map((v) => ({ id: String(v.device_id), lat: v.latitude!, lon: v.longitude!, heading: v.heading,
      status: v.status, label: vehicleLabel(v) })));
    map.current?.fitTo(located.map((v) => ({ lat: v.latitude!, lon: v.longitude! })), 13);
  }, [tracked]);

  if (summary.isLoading) return <SkeletonRows rows={6} />;
  const s = summary.data;
  const chart = STATUS_ORDER.map((k) => ({ key: k, name: t(`status.${k}`), value: byStatus[k] ?? 0 })).filter((d) => d.value > 0);
  const healthKinds = ["never_connected", "offline_over_24h", "weak_signal", "low_tracker_battery"] as const;
  const healthCount = healthKinds.reduce((n, k) => n + (s?.health[k]?.length ?? 0), 0);
  return (
    <>
      <PageHeader title={t("dashboard.title")} lede={membership ? membership.organization.name : undefined}
        actions={canLive ? <Link to="/app/live" className="rounded-md bg-ink px-4 py-2 text-sm font-semibold text-white hover:bg-ink-deep">{t("dashboard.openMap")}</Link> : undefined} />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
        <Card testId="card-vehicles" label={t("dashboard.vehicles")} value={s?.vehicles ?? 0} />
        <Card testId="card-moving" label={t("status.ONLINE_MOVING")} value={byStatus.ONLINE_MOVING ?? 0} status="ONLINE_MOVING" />
        <Card testId="card-idle" label={t("status.ONLINE_IDLE")} value={byStatus.ONLINE_IDLE ?? 0} status="ONLINE_IDLE" />
        <Card testId="card-stopped" label={t("status.ONLINE_STOPPED")} value={byStatus.ONLINE_STOPPED ?? 0} status="ONLINE_STOPPED" />
        <Card testId="card-offline" label={t("status.OFFLINE")} value={byStatus.OFFLINE ?? 0} status="OFFLINE" />
        <Card testId="card-nogps" label={t("status.NO_GPS")} value={byStatus.NO_GPS ?? 0} status="NO_GPS" />
        <Card testId="card-online" label={t("dashboard.onlineDevices")} value={`${online}/${s?.devices ?? 0}`} />
        <Card testId="card-trips-today" label={t("dashboardToday.trips")} value={s?.trips_today ?? 0} />
        <Card testId="card-distance-today" label={t("dashboardToday.distance")} value={`${s?.distance_today_km ?? 0} km`} />
      </div>
      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        {canLive && (
          <section className="rounded-lg border border-line bg-panel p-4 lg:col-span-2">
            <h2 className="mb-3 font-bold">{t("dashboard.fleetMap")}</h2>
            <div ref={mapEl} className="h-80 w-full overflow-hidden rounded-md bg-[#e9eeec]" aria-label={t("dashboard.fleetMap")} />
          </section>)}
        <section className="rounded-lg border border-line bg-panel p-4">
          <h2 className="mb-3 font-bold">{t("dashboard.statusDistribution")}</h2>
          {chart.length === 0 ? <p className="text-sm text-ink-soft">{t("live.noTrackers")}</p> : (
            <div className="h-64" role="img" aria-label={chart.map((d) => `${d.name}: ${d.value}`).join(", ")}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chart} layout="vertical" margin={{ left: 8, right: 16 }}>
                  <XAxis type="number" allowDecimals={false} /><YAxis type="category" dataKey="name" width={110} tick={{ fontSize: 12 }} />
                  <Tooltip /><Bar dataKey="value">{chart.map((d) => <Cell key={d.key} fill={STATUS_COLORS[d.key]} />)}</Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>)}
          {(s?.vehicles_without_tracker ?? 0) > 0 && <p className="mt-2 text-sm text-ink-soft">{t("dashboard.withoutTracker", { count: s?.vehicles_without_tracker })}</p>}
        </section>
      </div>
      <section className="mt-6 rounded-lg border border-line bg-panel p-4" data-testid="device-health">
        <h2 className="mb-3 font-bold">{t("dashboard.deviceHealth")}</h2>
        {healthCount === 0 ? <p className="text-sm text-moving">{t("dashboard.allHealthy")}</p> : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {healthKinds.filter((k) => s?.health[k]?.length).map((k) => (
              <div key={k}>
                <h3 className="text-sm font-semibold">{t(`dashboard.health.${k}`)} ({s?.health[k].length})</h3>
                <ul className="mt-1 space-y-0.5 text-sm">{s?.health[k].slice(0, 5).map((d) => (
                  <li key={d.device_id} className="flex justify-between gap-2"><span className="truncate">{d.name}</span>
                    <span className="shrink-0 text-ink-soft"><RelativeTime iso={d.last_packet_at} /></span></li>))}</ul>
              </div>))}
          </div>)}
      </section>
      <div className="mt-4"><DashboardAlerts /></div>
    </>
  );
}
