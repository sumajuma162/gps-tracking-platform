import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Download, Pause, Play, RotateCcw, Square } from "lucide-react";
import { Area, AreaChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, errorMessage } from "../../services/api";
import { useApiQuery, type Page } from "../../services/hooks";
import { hasPermission, useAuth } from "../../store/auth";
import { createMap } from "../../maps";
import type { MapAdapter } from "../../maps/types";
import { Button, EmptyState, ErrorState, Field, PageHeader, Select, SkeletonRows, TextInput, formatDateTime } from "../../components/ui";
import { toast } from "../../components/Toast";
import { RATES, advance, formatDuration, sampleAt, type RoutePoint } from "./playback";

interface Stop { start: string; end: string; duration_s: number; latitude: number; longitude: number; engine_off: boolean }
interface FenceEvent { event: "enter" | "exit" | "dwell"; geofence: string; at: number; lat: number; lon: number; delayed: boolean }
interface History { points: RoutePoint[]; stops: Stop[]; speed: [number, number][]; speeding: [number, number, number][];
  geofence_events?: FenceEvent[];
  summary: { points: number; returned_points: number; distance_km: number; max_speed_kmh: number; avg_moving_speed_kmh: number;
    driving_s: number; idle_s: number; stopped_s: number; start: string | null; end: string | null; speeding_limit_kmh: number } }
interface MapSettings { tile_url: string; attribution: string; center: [number, number]; zoom: number }

/** datetime-local value (browser local time) <-> ISO instant. */
const toLocalInput = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
const fromLocalInput = (v: string) => new Date(v).toISOString();

function preset(kind: "today" | "yesterday" | "last24"): [string, string] {
  const now = new Date();
  const midnight = new Date(now); midnight.setHours(0, 0, 0, 0);
  if (kind === "today") return [midnight.toISOString(), now.toISOString()];
  if (kind === "yesterday") return [new Date(midnight.getTime() - 86_400_000).toISOString(), midnight.toISOString()];
  return [new Date(now.getTime() - 86_400_000).toISOString(), now.toISOString()];
}

export function HistoryPage() {
  const { t } = useTranslation();
  const auth = useAuth();
  const canExport = hasPermission(auth, "reports.export");
  const [params, setParams] = useSearchParams();
  const vehicles = useApiQuery<Page<{ id: number; registration_number: string }>>("/vehicles/", { page_size: 200 });
  const settings = useApiQuery<MapSettings>("/settings/map/");
  const [def0, def1] = useMemo(() => preset("today"), []);
  const [vehicle, setVehicle] = useState(params.get("vehicle") ?? "");
  const [from, setFrom] = useState(toLocalInput(new Date(params.get("from") ?? def0)));
  const [to, setTo] = useState(toLocalInput(new Date(params.get("to") ?? def1)));
  const query = params.get("vehicle") && params.get("from") && params.get("to")
    ? { vehicle: params.get("vehicle"), start: params.get("from"), end: params.get("to"), zoom: 15 } : null;
  const hist = useApiQuery<History>("/tracking/history/", query ?? undefined, { enabled: query !== null });
  const points = useMemo(() => hist.data?.points ?? [], [hist.data]);

  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<MapAdapter | null>(null);
  const [time, setTime] = useState(0);              // playback time shown in the UI (<= 10 updates/s)
  const clock = useRef(0);                          // exact playback time, advanced every frame
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState<number>(16);
  const t0 = points[0]?.[0] ?? 0, t1 = points[points.length - 1]?.[0] ?? 0;

  useEffect(() => {
    if (!mapEl.current || !settings.data || map.current) return;
    map.current = createMap(mapEl.current, { tileUrl: settings.data.tile_url, attribution: settings.data.attribution,
      center: settings.data.center, zoom: settings.data.zoom });
    return () => { map.current?.destroy(); map.current = null; };
  }, [settings.data]);

  // Zoomed in to street level: fetch a more detailed line for just the visible area (spec A12).
  const detailQuery = useRef(query);
  detailQuery.current = query;
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    let timer: number | undefined;
    let token = 0;
    m.onViewChange((bounds, zoom) => {
      window.clearTimeout(timer);
      const q = detailQuery.current;
      if (!q || zoom < 15) { m.setDetail([]); return; }
      timer = window.setTimeout(async () => {
        const mine = ++token;
        try {
          const { data } = await api.get<{ segments: [number, number, number][][] }>("/tracking/history/",
            { params: { ...q, zoom, bbox: bounds.map((v) => v.toFixed(5)).join(",") } });
          if (mine === token) m.setDetail(data.segments.map((seg) => seg.map((p) => ({ lat: p[1], lon: p[2] }))));
        } catch { /* the simplified route stays visible */ }
      }, 400);
    });
  }, [settings.data]);

  const seek = useCallback((target: number) => {
    clock.current = target;
    setTime(target);
    const s = sampleAt(points, target);
    map.current?.setCursor(s ? { lat: s.lat, lon: s.lon, heading: s.heading } : null);
  }, [points]);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    setPlaying(false);
    const speeding = hist.data?.speeding ?? [];
    const highlights = speeding.map(([a, b]) => {   // each speeding stretch, cut exactly at its start and end times
      const inner = points.filter((p) => p[0] > a && p[0] < b).map((p) => ({ lat: p[1], lon: p[2] }));
      const ends = [sampleAt(points, a), sampleAt(points, b)];
      return [...(ends[0] ? [{ lat: ends[0].lat, lon: ends[0].lon }] : []), ...inner, ...(ends[1] ? [{ lat: ends[1].lat, lon: ends[1].lon }] : [])];
    });
    m.setRoute(points.length > 1 ? points.map((p) => ({ lat: p[1], lon: p[2] })) : null, { highlights, arrows: true });
    m.setDetail([]);
    const stops = hist.data?.stops ?? [];
    m.setPins(points.length ? [
      { id: "start", kind: "start" as const, lat: points[0][1], lon: points[0][2], label: t("history.start") },
      ...stops.map((s, i) => ({ id: `stop${i}`, kind: "stop" as const, lat: s.latitude, lon: s.longitude,
        label: t("history.stopLabel", { n: i + 1, duration: formatDuration(s.duration_s) }) })),
      { id: "end", kind: "end" as const, lat: points[points.length - 1][1], lon: points[points.length - 1][2], label: t("history.end") },
      ...(hist.data?.geofence_events ?? []).map((e, i) => ({ id: `g${i}`, kind: "event" as const, lat: e.lat, lon: e.lon,
        label: t(`history.fenceEvent.${e.event}`, { name: e.geofence }) })),
    ] : []);
    if (points.length) { m.fitTo(points.map((p) => ({ lat: p[1], lon: p[2] })), 16); seek(points[0][0]); }
    else m.setCursor(null);
  }, [points, hist.data, seek, t]);

  useEffect(() => {
    if (!playing) return;
    let frame = 0, last = performance.now(), lastUi = 0;
    const step = (now: number) => {
      clock.current = advance(clock.current, now - last, rate, t1);
      last = now;
      const s = sampleAt(points, clock.current);
      if (s) map.current?.setCursor({ lat: s.lat, lon: s.lon, heading: s.heading });
      if (now - lastUi > 100) { setTime(clock.current); lastUi = now; }
      if (clock.current >= t1) { setTime(t1); setPlaying(false); return; }
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [playing, rate, points, t1]);

  const show = (start = fromLocalInput(from), end = fromLocalInput(to)) => {
    if (!vehicle) return;
    setParams({ vehicle, from: start, to: end });
  };
  const applyPreset = (kind: "today" | "yesterday" | "last24") => {
    const [a, b] = preset(kind);
    setFrom(toLocalInput(new Date(a))); setTo(toLocalInput(new Date(b)));
    if (vehicle) show(a, b);
  };
  const download = async (type: "csv" | "gpx" | "kml") => {
    if (!query) return;
    try {
      const resp = await api.get<Blob>("/tracking/history/export/", { params: { ...query, type }, responseType: "blob" });
      const name = /filename="([^"]+)"/.exec(String(resp.headers["content-disposition"] ?? ""))?.[1] ?? `route.${type}`;
      const href = URL.createObjectURL(resp.data);
      const a = Object.assign(document.createElement("a"), { href, download: name });
      document.body.appendChild(a); a.click(); a.remove();
      window.setTimeout(() => URL.revokeObjectURL(href), 10_000);
    } catch (err) { toast.error(errorMessage(err, t("errors.generic"), t("errors.network"))); }
  };

  const current = sampleAt(points, time);
  const ignitionNow = current ? points[current.index][5] : null;
  // Speed comes from the time-sampled series, not from the route points (those are kept for geometry).
  const speedSeries = useMemo(() => hist.data?.speed ?? [], [hist.data]);
  const chart = useMemo(() => speedSeries.map(([t, v]) => ({ t, speed: v })), [speedSeries]);
  const stopSpans = useMemo(() => (hist.data?.stops ?? []).map((st) => [Date.parse(st.start) / 1000, Date.parse(st.end) / 1000]),
    [hist.data]);
  const speedNow = useMemo(() => {
    if (!speedSeries.length) return null;
    // Inside a detected stop the vehicle is standing still (a bucket's max may include the braking before it).
    if (stopSpans.some(([a, b]) => time >= a && time <= b)) return 0;
    let lo = 0, hi = speedSeries.length - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (speedSeries[mid][0] <= time) lo = mid; else hi = mid - 1; }
    return speedSeries[lo][1];
  }, [speedSeries, stopSpans, time]);
  const s = hist.data?.summary;
  return (
    <div data-testid="history">
      <PageHeader title={t("nav.history")} lede={t("history.lede")} />
      <form className="mb-4 flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); show(); }}>
        <div className="w-48"><Field id="h-vehicle" label={t("devices.vehicle")}>
          <Select id="h-vehicle" value={vehicle} onChange={(e) => setVehicle(e.target.value)}>
            <option value="">{t("res.choose")}</option>
            {vehicles.data?.results.map((v) => <option key={v.id} value={v.id}>{v.registration_number}</option>)}
          </Select></Field></div>
        <div className="w-52"><Field id="h-from" label={t("history.from")}><TextInput id="h-from" type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} /></Field></div>
        <div className="w-52"><Field id="h-to" label={t("history.to")}><TextInput id="h-to" type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} /></Field></div>
        <Button type="submit" disabled={!vehicle}>{t("history.show")}</Button>
        <div className="flex gap-1" role="group" aria-label={t("history.presets")}>
          {(["today", "yesterday", "last24"] as const).map((k) => <Button key={k} type="button" variant="ghost" onClick={() => applyPreset(k)}>{t(`history.preset.${k}`)}</Button>)}
        </div>
      </form>

      {hist.isError && <div className="mb-4"><ErrorState message={errorMessage(hist.error, t("errors.generic"), t("errors.network"))} /></div>}
      {s && (
        <dl className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-7" data-testid="history-summary">
          {[[t("history.distance"), `${s.distance_km.toLocaleString()} km`], [t("history.driving"), formatDuration(s.driving_s)],
            [t("history.idle"), formatDuration(s.idle_s)], [t("history.stopped"), formatDuration(s.stopped_s)],
            [t("history.maxSpeed"), `${Math.round(s.max_speed_kmh)} km/h`], [t("history.avgSpeed"), `${Math.round(s.avg_moving_speed_kmh)} km/h`],
            [t("history.stops"), String(hist.data?.stops.length ?? 0)]].map(([k, v]) => (
            <div key={k} className="rounded-md border border-line bg-panel p-3"><dt className="text-xs text-ink-soft">{k}</dt><dd className="font-display text-xl font-bold">{v}</dd></div>))}
        </dl>)}

      <div className="grid gap-4 lg:grid-cols-[1fr_18rem]">
        <section className="overflow-hidden rounded-lg border border-line bg-panel">
          <div ref={mapEl} className="h-[52vh] min-h-80 w-full bg-[#e9eeec]" aria-label={t("nav.history")} data-testid="history-map" />
          {points.length > 1 && (
            <div className="space-y-2 border-t border-line p-3" data-testid="playback">
              <div className="flex flex-wrap items-center gap-3">
                <Button aria-label={playing ? t("history.pause") : t("history.play")} onClick={() => {
                  if (!playing && clock.current >= t1) seek(t0);
                  setPlaying((p) => !p);
                }}>{playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}</Button>
                <Button variant="secondary" aria-label={t("history.stop")} onClick={() => { setPlaying(false); seek(t0); }}><Square className="h-4 w-4" /></Button>
                <Button variant="secondary" aria-label={t("history.restart")} onClick={() => { seek(t0); setPlaying(true); }}><RotateCcw className="h-4 w-4" /></Button>
                <label className="flex items-center gap-2 text-sm"><span>{t("history.speed")}</span>
                  <Select aria-label={t("history.speed")} value={rate} onChange={(e) => setRate(Number(e.target.value))} className="w-24">
                    {RATES.map((r) => <option key={r} value={r}>{r}×</option>)}</Select></label>
                <span className="font-mono text-sm" data-testid="playback-time" data-time={time.toFixed(1)}>{formatDateTime(new Date(time * 1000).toISOString())}</span>
              </div>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-sm sm:grid-cols-4" data-testid="playback-panel">
                <div><dt className="inline text-ink-soft">{t("diag.speed")}: </dt><dd className="inline" data-testid="playback-speed">{speedNow === null ? "—" : `${Math.round(speedNow)} km/h`}</dd></div>
                <div><dt className="inline text-ink-soft">{t("live.heading")}: </dt><dd className="inline">{current?.heading == null ? "—" : `${Math.round(current.heading)}°`}</dd></div>
                <div><dt className="inline text-ink-soft">{t("diag.field.ignition")}: </dt><dd className="inline">{ignitionNow == null ? "—" : ignitionNow ? t("diag.on") : t("diag.off")}</dd></div>
                <div><dt className="inline text-ink-soft">{t("diag.position")}: </dt><dd className="inline font-mono" data-testid="playback-coordinates">{current ? `${current.lat.toFixed(5)}, ${current.lon.toFixed(5)}` : "—"}</dd></div>
              </dl>
              <div className="relative h-2" aria-hidden="true" data-testid="timeline-ticks">
                {(hist.data?.stops ?? []).map((st) => {
                  const at = (Date.parse(st.start) / 1000 - t0) / Math.max(1, t1 - t0);
                  return <span key={st.start} className="absolute top-0 h-2 w-0.5 bg-marking" style={{ left: `${at * 100}%` }} />;
                })}
                {(hist.data?.speeding ?? []).map(([a, b]) => (
                  <span key={a} className="absolute top-0 h-2 bg-critical/80" style={{ left: `${((a - t0) / Math.max(1, t1 - t0)) * 100}%`, width: `${Math.max(0.3, ((b - a) / Math.max(1, t1 - t0)) * 100)}%` }} />))}
              </div>
              <input type="range" aria-label={t("history.timeline")} min={t0} max={t1} step={1} value={time}
                onChange={(e) => seek(Number(e.target.value))} className="w-full accent-[var(--color-ink)]" />
              <div className="h-24" role="img" aria-label={t("history.speedChart")}>
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={chart} margin={{ top: 4, right: 4, bottom: 0, left: -24 }}
                    onClick={(e) => { const x = (e as { activeLabel?: number } | null)?.activeLabel; if (typeof x === "number") seek(x); }}>
                    <XAxis dataKey="t" type="number" domain={[t0, t1]} hide />
                    <YAxis width={44} tick={{ fontSize: 11 }} allowDecimals={false} />
                    <Tooltip labelFormatter={(v) => formatDateTime(new Date(Number(v) * 1000).toISOString())} formatter={(v) => [`${v} km/h`, t("diag.speed")]} />
                    <Area type="stepAfter" dataKey="speed" stroke="#14323b" fill="#14323b" fillOpacity={0.15} isAnimationActive={false} dot={false} />
                    <ReferenceLine x={time} stroke="#e8a317" strokeWidth={2} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>)}
        </section>
        <aside className="space-y-4">
          {hist.isLoading && query ? <SkeletonRows rows={4} /> : !query ? <EmptyState message={t("history.choose")} />
            : points.length === 0 ? <EmptyState message={t("history.noData")} /> : (
              <section className="rounded-lg border border-line bg-panel p-3">
                <h2 className="mb-2 font-bold">{t("history.stops")} ({hist.data?.stops.length ?? 0})</h2>
                <ol className="max-h-80 space-y-1 overflow-y-auto text-sm" aria-label={t("history.stops")}>
                  {hist.data?.stops.map((st, i) => (
                    <li key={st.start}><button type="button" className="w-full rounded px-2 py-1.5 text-left hover:bg-paper"
                      onClick={() => { seek(new Date(st.start).getTime() / 1000); map.current?.panTo({ lat: st.latitude, lon: st.longitude }); }}>
                      <span className="font-semibold">{i + 1}. {formatDuration(st.duration_s)}</span>{st.engine_off ? ` · ${t("history.engineOff")}` : ""}
                      <span className="block text-xs text-ink-soft">{formatDateTime(st.start)}</span></button></li>))}
                  {hist.data?.stops.length === 0 && <li className="text-ink-soft">{t("history.noStops")}</li>}
                </ol>
              </section>)}
          {canExport && points.length > 0 && (
            <section className="rounded-lg border border-line bg-panel p-3">
              <h2 className="mb-2 font-bold">{t("history.download")}</h2>
              <div className="flex flex-wrap gap-2">{(["csv", "gpx", "kml"] as const).map((f) => (
                <Button key={f} variant="secondary" onClick={() => void download(f)}><Download className="mr-1 h-4 w-4" aria-hidden />{f.toUpperCase()}</Button>))}</div>
              <p className="mt-2 text-xs text-ink-soft">{t("history.downloadHelp")}</p>
            </section>)}
          {s && <p className="text-xs text-ink-soft">{t("history.pointsNote", { shown: s.returned_points, total: s.points })}</p>}
        </aside>
      </div>
    </div>
  );
}
