import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { api, errorMessage, fieldErrors } from "../../services/api";
import { useApiQuery, type Page } from "../../services/hooks";
import { hasPermission, useAuth } from "../../store/auth";
import { createMap } from "../../maps";
import type { LatLon, MapAdapter, ShapeSpec } from "../../maps/types";
import { Button, EmptyState, Field, PageHeader, SkeletonRows, TextInput } from "../../components/ui";
import { Dialog } from "../../components/Dialog";
import { toast } from "../../components/Toast";

interface Geofence { id: number; name: string; description: string; kind: "circle" | "polygon"; center_lat: number | null;
  center_lon: number | null; radius_m: number | null; polygon: [number, number][]; dwell_minutes: number | null; color: string; is_active: boolean }
interface MapSettings { tile_url: string; attribution: string; center: [number, number]; zoom: number }
interface Draft { id?: number; name: string; kind: "circle" | "polygon"; center: LatLon | null; radius: string; points: LatLon[];
  dwell: string; color: string }

const EMPTY: Draft = { name: "", kind: "polygon", center: null, radius: "200", points: [], dwell: "", color: "#2563eb" };

export function toShape(g: Geofence): ShapeSpec {
  return g.kind === "circle"
    ? { id: String(g.id), kind: "circle", color: g.color, label: g.name, center: { lat: g.center_lat ?? 0, lon: g.center_lon ?? 0 }, radius_m: g.radius_m ?? 0 }
    : { id: String(g.id), kind: "polygon", color: g.color, label: g.name, polygon: g.polygon.map(([lat, lon]) => ({ lat, lon })) };
}

export function GeofencesPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const canManage = hasPermission(useAuth(), "geofences.manage");
  const list = useApiQuery<Page<Geofence>>("/geofences/", { page_size: 200 });
  const settings = useApiQuery<MapSettings>("/settings/map/");
  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<MapAdapter | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState<Geofence | null>(null);
  const fences = useMemo(() => list.data?.results ?? [], [list.data]);

  useEffect(() => {
    if (!mapEl.current || !settings.data || map.current) return;
    map.current = createMap(mapEl.current, { tileUrl: settings.data.tile_url, attribution: settings.data.attribution,
      center: settings.data.center, zoom: settings.data.zoom });
    return () => { map.current?.destroy(); map.current = null; };
  }, [settings.data]);

  useEffect(() => {
    const shapes = fences.filter((g) => g.id !== draft?.id).map(toShape);
    if (draft) {
      if (draft.kind === "circle" && draft.center) shapes.push({ id: "draft", kind: "circle", color: draft.color, label: draft.name, center: draft.center, radius_m: Number(draft.radius) || 0, dashed: true });
      if (draft.kind === "polygon" && draft.points.length) shapes.push({ id: "draft", kind: "polygon", color: draft.color, label: draft.name, polygon: draft.points, dashed: true });
    }
    map.current?.setShapes(shapes);
  }, [fences, draft, settings.data]);

  useEffect(() => {
    map.current?.onMapClick(draft ? (p) => setDraft((d) => d && (d.kind === "circle" ? { ...d, center: p } : { ...d, points: [...d.points, p] })) : null);
  }, [draft, settings.data]);

  const edit = (g: Geofence) => setDraft({ id: g.id, name: g.name, kind: g.kind, color: g.color, dwell: g.dwell_minutes ? String(g.dwell_minutes) : "",
    center: g.center_lat != null && g.center_lon != null ? { lat: g.center_lat, lon: g.center_lon } : null, radius: String(g.radius_m ?? 200),
    points: g.polygon.map(([lat, lon]) => ({ lat, lon })) });
  const refresh = () => void qc.invalidateQueries({ predicate: (q) => String(q.queryKey[1]).startsWith("/geofences") });

  const save = async () => {
    if (!draft) return;
    setBusy(true); setErrors({});
    const body = { name: draft.name.trim(), kind: draft.kind, color: draft.color, dwell_minutes: draft.dwell ? Number(draft.dwell) : null,
      ...(draft.kind === "circle" ? { center_lat: draft.center?.lat ?? null, center_lon: draft.center?.lon ?? null, radius_m: Number(draft.radius) || null }
        : { polygon: draft.points.map((p) => [Number(p.lat.toFixed(6)), Number(p.lon.toFixed(6))]) }) };
    try {
      if (draft.id) await api.patch(`/geofences/${draft.id}/`, body); else await api.post("/geofences/", body);
      toast.success(t("res.saved")); setDraft(null); refresh();
    } catch (err) {
      const f = fieldErrors(err);
      setErrors(Object.keys(f).length ? f : { _: errorMessage(err, t("errors.generic"), t("errors.network")) });
    } finally { setBusy(false); }
  };
  const remove = async (g: Geofence) => {
    try { await api.delete(`/geofences/${g.id}/`); toast.success(t("res.deleted")); refresh(); }
    catch (err) { toast.error(errorMessage(err, t("errors.generic"), t("errors.network"))); }
    setDeleting(null);
  };

  return (
    <>
      <PageHeader title={t("nav.geofences")} lede={t("geofences.lede")}
        actions={canManage && !draft ? <Button onClick={() => setDraft({ ...EMPTY })}>{t("geofences.new")}</Button> : undefined} />
      <div className="grid gap-4 lg:grid-cols-[20rem_1fr]">
        <aside className="space-y-3">
          {draft ? (
            <section className="space-y-3 rounded-lg border border-line bg-panel p-4" data-testid="geofence-editor">
              <h2 className="font-bold">{draft.id ? t("geofences.edit") : t("geofences.new")}</h2>
              {errors._ && <p role="alert" className="text-sm text-critical">{errors._}</p>}
              <Field id="g-name" label={`${t("fields.name")} *`} error={errors.name}>
                <TextInput id="g-name" value={draft.name} placeholder="Kurasini depot" onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></Field>
              <fieldset className="flex gap-4 text-sm"><legend className="sr-only">{t("geofences.shape")}</legend>
                {(["polygon", "circle"] as const).map((k) => (
                  <label key={k} className="flex items-center gap-1.5"><input type="radio" name="g-kind" checked={draft.kind === k}
                    onChange={() => setDraft({ ...draft, kind: k })} />{t(`geofences.kind.${k}`)}</label>))}
              </fieldset>
              <p className="rounded bg-paper p-2 text-sm" role="status">{draft.kind === "polygon"
                ? t("geofences.drawPolygon", { count: draft.points.length }) : draft.center ? t("geofences.centerSet") : t("geofences.drawCircle")}</p>
              {(errors.polygon || errors.radius_m) && <p role="alert" className="text-sm text-critical">{errors.polygon ?? errors.radius_m}</p>}
              {draft.kind === "polygon" ? (
                <div className="flex gap-2">
                  <Button variant="secondary" disabled={!draft.points.length} onClick={() => setDraft({ ...draft, points: draft.points.slice(0, -1) })}>{t("geofences.undo")}</Button>
                  <Button variant="secondary" disabled={!draft.points.length} onClick={() => setDraft({ ...draft, points: [] })}>{t("geofences.clear")}</Button>
                </div>
              ) : (
                <Field id="g-radius" label={t("geofences.radius")}><TextInput id="g-radius" type="number" min={10} value={draft.radius}
                  onChange={(e) => setDraft({ ...draft, radius: e.target.value })} /></Field>)}
              <div className="grid grid-cols-2 gap-3">
                <Field id="g-dwell" label={t("geofences.dwell")} hint={t("geofences.dwellHint")} error={errors.dwell_minutes}>
                  <TextInput id="g-dwell" type="number" min={1} value={draft.dwell} onChange={(e) => setDraft({ ...draft, dwell: e.target.value })} /></Field>
                <Field id="g-color" label={t("geofences.color")} error={errors.color}>
                  <input id="g-color" type="color" value={draft.color} onChange={(e) => setDraft({ ...draft, color: e.target.value })} className="h-10 w-full rounded border border-line" /></Field>
              </div>
              <div className="flex justify-end gap-2 border-t border-line pt-3">
                <Button variant="secondary" onClick={() => { setDraft(null); setErrors({}); }}>{t("common.cancel")}</Button>
                <Button busy={busy} onClick={() => void save()}>{t("common.save")}</Button>
              </div>
            </section>
          ) : null}
          {list.isLoading ? <SkeletonRows rows={3} /> : fences.length === 0 && !draft ? <EmptyState message={t("geofences.empty")} /> : (
            <ul className="divide-y divide-line rounded-lg border border-line bg-panel" aria-label={t("nav.geofences")}>
              {fences.map((g) => (
                <li key={g.id} className="flex items-center gap-3 px-3 py-2.5" data-geofence={g.name}>
                  <span className="h-4 w-4 shrink-0 rounded-sm border" style={{ background: `${g.color}33`, borderColor: g.color }} aria-hidden />
                  <span className="min-w-0 flex-1"><span className="block truncate font-semibold">{g.name}</span>
                    <span className="block text-xs text-ink-soft">{t(`geofences.kind.${g.kind}`)}{g.kind === "circle" ? ` · ${Math.round(g.radius_m ?? 0)} m` : ` · ${g.polygon.length} ${t("geofences.points")}`}{g.dwell_minutes ? ` · ${t("geofences.dwellShort", { n: g.dwell_minutes })}` : ""}</span></span>
                  {canManage && <><Button variant="ghost" onClick={() => edit(g)}>{t("res.edit")}</Button>
                    <Button variant="ghost" onClick={() => setDeleting(g)}>{t("res.delete")}</Button></>}
                </li>))}
            </ul>)}
        </aside>
        <section className="overflow-hidden rounded-lg border border-line bg-panel">
          <div ref={mapEl} className="h-[65vh] min-h-80 w-full bg-[#e9eeec]" aria-label={t("geofences.map")} data-testid="geofence-map" />
        </section>
      </div>
      <Dialog open={!!deleting} title={t("res.delete")} onClose={() => setDeleting(null)}>
        <p>{t("geofences.deleteConfirm", { name: deleting?.name ?? "" })}</p>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setDeleting(null)}>{t("common.cancel")}</Button>
          <Button variant="danger" onClick={() => deleting && void remove(deleting)}>{t("res.delete")}</Button>
        </div>
      </Dialog>
    </>
  );
}
