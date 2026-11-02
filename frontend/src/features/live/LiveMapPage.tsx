import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Crosshair, Expand, WifiOff, X } from "lucide-react";
import { useApiQuery, type Page } from "../../services/hooks";
import { startTracking } from "../../services/tracking";
import { STATUS_ORDER, useLive, vehicleLabel, type LiveVehicle } from "../../store/live";
import { createMap } from "../../maps";
import type { MapAdapter } from "../../maps/types";
import { STATUS_COLORS } from "../../maps/types";
import { TextInput, Select, formatDateTime } from "../../components/ui";
import { RelativeTime } from "./RelativeTime";
import { toShape } from "../geofences/GeofencesPage";
import { ShareDialog } from "../sharing/ShareDialog";
import { hasPermission, useAuth } from "../../store/auth";

interface MapSettings { tile_url: string; attribution: string; center: [number, number]; zoom: number }
const VIEWPORT_THRESHOLD = 500;

export function StatusDot({ status }: { status: string }) {
  return <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: STATUS_COLORS[status] ?? STATUS_COLORS.UNKNOWN }} aria-hidden />;
}

function useLiveFeed() {
  const handle = useRef<ReturnType<typeof startTracking> | null>(null);
  useEffect(() => { handle.current = startTracking(); return () => handle.current?.close(); }, []);
  return handle;
}

export function LiveMapPage() {
  const { t } = useTranslation();
  const feed = useLiveFeed();
  const vehicles = useLive((s) => s.vehicles);
  const connection = useLive((s) => s.connection);
  const settings = useApiQuery<MapSettings>("/settings/map/");
  const groups = useApiQuery<Page<{ id: number; name: string }>>("/vehicle-groups/", { page_size: 200 }, { retry: false });
  const [search, setSearch] = useState("");
  const [statuses, setStatuses] = useState<Set<string>>(new Set());
  const [group, setGroup] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const [follow, setFollow] = useState(false);
  const canFences = hasPermission(useAuth(), "geofences.view");
  const [showFences, setShowFences] = useState(false);
  const fences = useApiQuery<Page<Parameters<typeof toShape>[0]>>("/geofences/", { page_size: 200 }, { enabled: canFences && showFences });
  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<MapAdapter | null>(null);
  const fitted = useRef(false);

  const all = useMemo(() => Object.values(vehicles).sort((a, b) => vehicleLabel(a).localeCompare(vehicleLabel(b))), [vehicles]);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const v of all) c[v.status] = (c[v.status] ?? 0) + 1;
    return c;
  }, [all]);
  const visible = useMemo(() => all.filter((v) => {
    if (statuses.size && !statuses.has(v.status)) return false;
    if (group && !v.vehicle?.groups.includes(Number(group))) return false;
    const q = search.trim().toLowerCase();
    return !q || [vehicleLabel(v), v.device_name, v.identifier, v.driver?.name ?? ""].some((s) => s.toLowerCase().includes(q));
  }), [all, statuses, group, search]);
  const located = visible.filter((v) => v.latitude !== null && v.longitude !== null);
  const current = selected !== null ? vehicles[selected] ?? null : null;

  useEffect(() => {
    if (!mapEl.current || !settings.data || map.current) return;
    map.current = createMap(mapEl.current, { tileUrl: settings.data.tile_url, attribution: settings.data.attribution,
      center: settings.data.center, zoom: settings.data.zoom, cluster: true });
    map.current.onMarkerClick((id) => { setSelected(Number(id)); });
    map.current.onViewChange((bounds) => { if (Object.keys(useLive.getState().vehicles).length > VIEWPORT_THRESHOLD) feed.current?.setViewport(bounds); });
    return () => { map.current?.destroy(); map.current = null; fitted.current = false; };
  }, [settings.data, feed]);

  useEffect(() => {
    map.current?.setMarkers(located.map((v) => ({ id: String(v.device_id), lat: v.latitude!, lon: v.longitude!, heading: v.heading,
      status: v.status, label: `${vehicleLabel(v)}: ${t(`status.${v.status}`)}`, selected: v.device_id === selected })));
    if (!fitted.current && located.length) { map.current?.fitTo(located.map((v) => ({ lat: v.latitude!, lon: v.longitude! }))); fitted.current = true; }
  }, [located, selected, t]);

  useEffect(() => {
    if (follow && current?.latitude != null && current.longitude != null) map.current?.panTo({ lat: current.latitude, lon: current.longitude });
  }, [follow, current?.latitude, current?.longitude]);

  useEffect(() => {   // geofence overlay toggle (spec A12)
    map.current?.setShapes(showFences ? (fences.data?.results ?? []).filter((g) => g.is_active).map(toShape) : []);
  }, [showFences, fences.data, settings.data]);

  const toggleStatus = (s: string) => setStatuses((prev) => { const n = new Set(prev); if (n.has(s)) n.delete(s); else n.add(s); return n; });
  const fitAll = () => map.current?.fitTo(located.map((v) => ({ lat: v.latitude!, lon: v.longitude! })));

  return (
    <div className="-mx-5 -my-8 flex h-[calc(100vh-3.25rem)] flex-col lg:flex-row" data-testid="live-map">
      <aside className="flex max-h-[45vh] flex-col border-b border-line bg-panel lg:max-h-none lg:w-80 lg:border-b-0 lg:border-r">
        <div className="space-y-3 border-b border-line p-3">
          <h1 className="text-xl font-bold">{t("nav.liveMap")}</h1>
          <label className="block"><span className="sr-only">{t("live.search")}</span>
            <TextInput id="live-search" placeholder={t("live.search")} value={search} onChange={(e) => setSearch(e.target.value)} /></label>
          <div className="flex flex-wrap gap-1.5" role="group" aria-label={t("live.filterStatus")}>
            {STATUS_ORDER.filter((s) => counts[s]).map((s) => (
              <button key={s} type="button" aria-pressed={statuses.has(s)} onClick={() => toggleStatus(s)}
                className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold ${statuses.has(s) ? "border-ink bg-ink text-white" : "border-line bg-panel"}`}>
                <StatusDot status={s} />{t(`status.${s}`)} <span className="opacity-70">{counts[s]}</span>
              </button>))}
          </div>
          {(groups.data?.results.length ?? 0) > 0 && (
            <label className="block"><span className="sr-only">{t("fields.groups")}</span>
              <Select value={group} onChange={(e) => setGroup(e.target.value)}>
                <option value="">{t("live.allGroups")}</option>
                {groups.data?.results.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
              </Select></label>)}
        </div>
        <ul className="flex-1 divide-y divide-line overflow-y-auto" aria-label={t("live.vehicles")}>
          {visible.map((v) => (
            <li key={v.device_id}>
              <button type="button" onClick={() => { setSelected(v.device_id); if (v.latitude != null && v.longitude != null) map.current?.panTo({ lat: v.latitude, lon: v.longitude }); }}
                aria-current={selected === v.device_id ? "true" : undefined} data-device-id={v.device_id}
                className={`flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-paper ${selected === v.device_id ? "bg-marking-soft" : ""}`}>
                <StatusDot status={v.status} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-semibold">{vehicleLabel(v)}</span>
                  <span className="block truncate text-xs text-ink-soft">{t(`status.${v.status}`)}{v.status === "ONLINE_MOVING" && v.speed_kmh != null ? ` · ${Math.round(v.speed_kmh)} km/h` : ""}</span>
                </span>
                <span className="shrink-0 text-xs text-ink-soft"><RelativeTime iso={v.last_packet_at} /></span>
              </button>
            </li>))}
          {visible.length === 0 && <li className="p-4 text-sm text-ink-soft">{all.length ? t("live.noMatch") : t("live.noTrackers")}</li>}
        </ul>
      </aside>
      <section className="relative min-h-[50vh] flex-1">
        <div ref={mapEl} className="h-full w-full bg-[#e9eeec]" aria-label={t("nav.liveMap")} />
        {connection !== "live" && connection !== "connecting" && (
          <div role="status" className="absolute left-1/2 top-3 z-[500] flex -translate-x-1/2 items-center gap-2 rounded-md bg-ink px-3 py-1.5 text-sm text-white shadow">
            <WifiOff className="h-4 w-4" aria-hidden />{connection === "polling" ? t("live.polling") : t("live.reconnecting")}
          </div>)}
        {!settings.data?.tile_url && settings.data && <p className="absolute bottom-2 left-2 z-[500] rounded bg-panel/90 px-2 py-1 text-xs text-ink-soft">{t("live.noTiles")}</p>}
        <div className="absolute right-3 top-3 z-[500] flex flex-col gap-2">
          <button type="button" onClick={fitAll} className="flex items-center gap-1.5 rounded-md border border-line bg-panel px-3 py-1.5 text-sm font-semibold shadow-sm hover:bg-paper">
            <Expand className="h-4 w-4" aria-hidden />{t("live.fitAll")}</button>
          {canFences && <button type="button" aria-pressed={showFences} onClick={() => setShowFences((v) => !v)}
            className={`rounded-md border px-3 py-1.5 text-sm font-semibold shadow-sm ${showFences ? "border-ink bg-ink text-white" : "border-line bg-panel hover:bg-paper"}`}>
            {t("live.geofences")}</button>}
        </div>
        {current && <VehiclePanel v={current} follow={follow} onFollow={() => setFollow((f) => !f)} onClose={() => { setSelected(null); setFollow(false); }} />}
      </section>
    </div>
  );
}

function VehiclePanel({ v, follow, onFollow, onClose }: { v: LiveVehicle; follow: boolean; onFollow: () => void; onClose: () => void }) {
  const canShare = hasPermission(useAuth(), "share_links.create");
  const [sharing, setSharing] = useState(false);
  const { t } = useTranslation();
  const fmt = (n: number | null, digits = 1, unit = "") => (n === null ? "—" : `${n.toFixed(digits)}${unit}`);
  const rows: [string, React.ReactNode][] = [
    [t("live.tracker"), `${v.device_name}`], ["IMEI", <span className="font-mono">{v.identifier}</span>],
    [t("vehicles.driver"), v.driver ? `${v.driver.name}${v.driver.phone ? ` · ${v.driver.phone}` : ""}` : "—"],
    [t("diag.position"), v.latitude !== null && v.longitude !== null ? <span className="font-mono" data-testid="panel-position">{v.latitude.toFixed(5)}, {v.longitude.toFixed(5)}</span> : t("diag.noFix")],
    [t("diag.speed"), v.speed_kmh === null ? "—" : `${Math.round(v.speed_kmh)} km/h`],
    [t("live.heading"), v.heading === null ? "—" : `${Math.round(v.heading)}°`],
    [t("diag.field.ignition"), v.ignition === null ? "—" : v.ignition ? t("diag.on") : t("diag.off")],
    [t("diag.satellites"), v.satellites ?? "—"],
    [t("diag.field.external_voltage"), fmt(v.external_voltage, 2, " V")], [t("diag.field.battery_voltage"), fmt(v.battery_voltage, 2, " V")],
    [t("diag.field.gsm_signal"), v.gsm_signal === null ? "—" : `${v.gsm_signal}/5`],
    [t("live.lastUpdate"), v.last_packet_at ? <>{formatDateTime(v.last_packet_at)} (<RelativeTime iso={v.last_packet_at} />)</> : "—"],
  ];
  return (
    <aside className="absolute bottom-3 left-3 right-3 z-[500] max-h-[60%] overflow-y-auto rounded-lg border border-line bg-panel p-4 shadow-lg sm:left-auto sm:w-80"
      aria-label={vehicleLabel(v)} data-testid="vehicle-panel">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div>
          <h2 className="text-lg font-bold">{vehicleLabel(v)}</h2>
          <p className="flex items-center gap-1.5 text-sm"><StatusDot status={v.status} />{t(`status.${v.status}`)}</p>
        </div>
        <button type="button" onClick={onClose} aria-label={t("common.close")} className="rounded p-1 hover:bg-paper"><X className="h-5 w-5" /></button>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        {rows.map(([k, val]) => <div key={k} className="contents"><dt className="text-ink-soft">{k}</dt><dd className="min-w-0 break-words">{val}</dd></div>)}
      </dl>
      <button type="button" aria-pressed={follow} onClick={onFollow}
        className={`mt-3 flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm font-semibold ${follow ? "border-ink bg-ink text-white" : "border-line hover:bg-paper"}`}>
        <Crosshair className="h-4 w-4" aria-hidden />{follow ? t("live.following") : t("live.follow")}</button>
      {v.vehicle && canShare && <button type="button" onClick={() => setSharing(true)}
        className="mt-2 rounded-md border border-line px-3 py-1.5 text-sm font-semibold hover:bg-paper">{t("share.create")}</button>}
      {sharing && v.vehicle && <ShareDialog vehicleId={v.vehicle.id} onClose={() => setSharing(false)} />}
    </aside>
  );
}
