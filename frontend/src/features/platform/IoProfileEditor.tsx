import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { api, errorMessage, type ApiErrorBody } from "../../services/api";
import { useApiQuery } from "../../services/hooks";
import { Badge, Button, ErrorState, PageHeader, Select, SkeletonRows, TextInput, formatDateTime } from "../../components/ui";
import { toast } from "../../components/Toast";
import axios from "axios";

export interface Mapping { io_id: number; kind: "field" | "sensor" | "event"; target: string; scale?: number; offset?: number;
  unit?: string; event_map?: Record<string, string> }
interface Profile { id: number; name: string; verified: boolean; used_by: string[];
  current: { version: number; mappings: Mapping[]; notes: string; created_at: string } }
interface Version { id: number; version: number; notes: string; created_by_email: string | null; created_at: string }
interface MapResult { fields: Record<string, number | boolean>; sensors: Record<string, number>; events: string[]; unmapped: Record<string, unknown> }
interface Preview { source: string; io: Record<string, number | string>; current: MapResult; draft: MapResult }

const FIELDS = ["ignition", "motion", "odometer_km", "engine_hours", "external_voltage", "battery_voltage", "battery_level", "gsm_signal", "hdop"];

interface Row { io_id: string; kind: Mapping["kind"]; target: string; scale: string; offset: string; unit: string; events: string }

const toRow = (m: Mapping): Row => ({ io_id: String(m.io_id), kind: m.kind, target: m.target, scale: m.scale === undefined ? "" : String(m.scale),
  offset: m.offset === undefined ? "" : String(m.offset), unit: m.unit ?? "",
  events: m.event_map ? Object.entries(m.event_map).map(([k, v]) => `${k}=${v}`).join(", ") : "" });

/** Rows -> mappings. Event values are written "1=towing, 2=harsh_braking" ("*" means any non-zero value). */
export function toMappings(rows: Row[]): Mapping[] {
  return rows.map((r) => {
    const m: Mapping = { io_id: Number(r.io_id), kind: r.kind, target: r.target.trim() };
    if (r.kind !== "event") {
      if (r.scale.trim() !== "") m.scale = Number(r.scale);
      if (r.offset.trim() !== "") m.offset = Number(r.offset);
      if (r.unit.trim() !== "") m.unit = r.unit.trim();
    } else {
      m.event_map = Object.fromEntries(r.events.split(",").map((p) => p.split("=").map((x) => x.trim())).filter((p) => p.length === 2 && p[0] && p[1]));
    }
    return m;
  });
}

export function IoProfileEditor() {
  const { t } = useTranslation();
  const { id } = useParams();
  const qc = useQueryClient();
  const profile = useApiQuery<Profile>(`/device-catalog/io-profiles/${id}/`);
  const versions = useApiQuery<Version[]>(`/device-catalog/io-profiles/${id}/versions/`);
  const [rows, setRows] = useState<Row[]>([]);
  const [notes, setNotes] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState<"preview" | "save" | null>(null);

  useEffect(() => { if (profile.data) setRows(profile.data.current.mappings.map(toRow)); }, [profile.data]);

  const update = (i: number, patch: Partial<Row>) => { setRows(rows.map((r, k) => (k === i ? { ...r, ...patch } : r))); setPreview(null); };
  const handle = (err: unknown) => {
    const details = axios.isAxiosError(err) ? (err.response?.data as ApiErrorBody | undefined)?.error?.details : undefined;
    const list = details && Array.isArray((details as Record<string, unknown>).mappings) ? (details as Record<string, string[]>).mappings : null;
    setErrors(list ?? [errorMessage(err, t("errors.generic"), t("errors.network"))]);
  };
  const runPreview = async () => {
    setBusy("preview"); setErrors([]);
    try { setPreview((await api.post<Preview>(`/device-catalog/io-profiles/${id}/preview/`, { mappings: toMappings(rows) })).data); }
    catch (err) { handle(err); } finally { setBusy(null); }
  };
  const save = async () => {
    setBusy("save"); setErrors([]);
    try {
      await api.patch(`/device-catalog/io-profiles/${id}/`, { mappings: toMappings(rows), notes });
      toast.success(t("io.saved")); setNotes(""); setPreview(null);
      void qc.invalidateQueries({ predicate: (x) => String(x.queryKey[1]).startsWith(`/device-catalog/io-profiles/${id}`) });
    } catch (err) { handle(err); } finally { setBusy(null); }
  };
  const rollback = async (version: number) => {
    try { await api.post(`/device-catalog/io-profiles/${id}/rollback/`, { version }); toast.success(t("io.rolledBack", { version }));
      void qc.invalidateQueries({ predicate: (x) => String(x.queryKey[1]).startsWith(`/device-catalog/io-profiles/${id}`) }); }
    catch (err) { toast.error(errorMessage(err, t("errors.generic"), t("errors.network"))); }
  };

  if (profile.isLoading) return <SkeletonRows />;
  if (profile.isError || !profile.data) return <ErrorState message={errorMessage(profile.error, t("errors.generic"), t("errors.network"))} />;
  const p = profile.data;
  const cell = "border-b border-line px-2 py-1.5";
  const resultFor = (r: MapResult, m: Mapping): string => {
    if (m.kind === "event") return r.events.filter((e) => Object.values(m.event_map ?? {}).includes(e)).join(", ") || "—";
    const v = m.kind === "field" ? r.fields[m.target] : r.sensors[m.target];
    return v === undefined ? "—" : String(v);
  };
  const draft = toMappings(rows);

  return (
    <div>
      <p className="mb-2 text-sm"><Link to="/app/device-catalog" className="underline">{t("nav.catalog")}</Link></p>
      <PageHeader title={`${p.name} · v${p.current.version}`} lede={t("io.lede", { models: p.used_by.join(", ") || "—" })}
        actions={!p.verified ? <Badge tone="warn">{t("catalog.verifiedNo")}</Badge> : <Badge tone="good">{t("catalog.verifiedYes")}</Badge>} />
      {errors.length > 0 && <div role="alert" className="mb-4 rounded-md border border-critical/30 bg-critical/5 p-3 text-sm text-critical"><ul className="list-disc pl-5">{errors.map((e) => <li key={e}>{e}</li>)}</ul></div>}
      <div className="overflow-x-auto rounded-md border border-line bg-panel">
        <table className="w-full min-w-[62rem] table-fixed text-left text-sm">
          <colgroup>
            <col className="w-20" /><col className="w-32" /><col className="w-48" /><col className="w-24" /><col className="w-20" />
            <col className="w-16" /><col />{preview && <><col className="w-24" /><col className="w-24" /></>}<col className="w-12" />
          </colgroup>
          <thead className="text-ink-soft"><tr>
            <th className={cell}>IO</th><th className={cell}>{t("catalog.kind")}</th><th className={cell}>{t("catalog.target")}</th>
            <th className={cell}>{t("io.scale")}</th><th className={cell}>{t("io.offset")}</th><th className={cell}>{t("io.unit")}</th>
            <th className={cell}>{t("io.events")}</th>{preview && <><th className={cell}>{t("io.current")}</th><th className={cell}>{t("io.draft")}</th></>}
            <th className={cell}><span className="sr-only">{t("res.delete")}</span></th></tr></thead>
          <tbody>{rows.map((r, i) => {
            const before = preview ? resultFor(preview.current, draft[i]) : "";
            const after = preview ? resultFor(preview.draft, draft[i]) : "";
            return (
              <tr key={i} data-io={r.io_id}>
                <td className={cell}><TextInput aria-label={t("io.ioId", { n: i + 1 })} inputMode="numeric" value={r.io_id} onChange={(e) => update(i, { io_id: e.target.value })} /></td>
                <td className={cell}><Select aria-label={t("catalog.kind")} value={r.kind} onChange={(e) => update(i, { kind: e.target.value as Row["kind"] })}>
                  <option value="field">{t("io.kind.field")}</option><option value="sensor">{t("io.kind.sensor")}</option><option value="event">{t("io.kind.event")}</option></Select></td>
                <td className={cell}>{r.kind === "field"
                  ? <Select aria-label={t("catalog.target")} value={r.target} onChange={(e) => update(i, { target: e.target.value })}>{FIELDS.map((f) => <option key={f} value={f}>{f}</option>)}</Select>
                  : <TextInput aria-label={t("catalog.target")} value={r.target} onChange={(e) => update(i, { target: e.target.value })} />}</td>
                {r.kind === "event" ? <td className={`${cell} text-center text-ink-soft`} colSpan={3} aria-hidden>—</td> : <>
                  <td className={cell}><TextInput aria-label={t("io.scaleFor", { io: r.io_id })} value={r.scale} placeholder="1" onChange={(e) => update(i, { scale: e.target.value })} /></td>
                  <td className={cell}><TextInput aria-label={t("io.offset")} value={r.offset} placeholder="0" onChange={(e) => update(i, { offset: e.target.value })} /></td>
                  <td className={cell}><TextInput aria-label={t("io.unit")} value={r.unit} onChange={(e) => update(i, { unit: e.target.value })} /></td></>}
                <td className={cell}>{r.kind === "event"
                  ? <TextInput aria-label={t("io.eventsFor", { io: r.io_id })} value={r.events} placeholder="1=towing, 2=…" onChange={(e) => update(i, { events: e.target.value })} />
                  : <span className="block text-center text-ink-soft" aria-hidden>—</span>}</td>
                {preview && <><td className={`${cell} font-mono`}>{before}</td>
                  <td className={`${cell} font-mono ${before !== after ? "bg-marking-soft font-semibold" : ""}`} data-testid={`draft-${r.io_id}`}>{after}</td></>}
                <td className={cell}><button type="button" aria-label={t("io.removeRow", { io: r.io_id })} onClick={() => { setRows(rows.filter((_, k) => k !== i)); setPreview(null); }}
                  className="rounded p-1.5 text-ink-soft hover:bg-paper hover:text-critical"><Trash2 className="h-4 w-4" /></button></td>
              </tr>);
          })}</tbody>
        </table>
      </div>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <Button variant="secondary" onClick={() => { setRows([...rows, { io_id: "", kind: "sensor", target: "", scale: "", offset: "", unit: "", events: "" }]); setPreview(null); }}>{t("io.addRow")}</Button>
        <Button variant="secondary" busy={busy === "preview"} onClick={() => void runPreview()}>{t("io.preview")}</Button>
        <label className="min-w-64 flex-1"><span className="mb-1 block text-sm font-semibold">{t("io.notes")}</span>
          <TextInput value={notes} placeholder={t("io.notesPlaceholder")} onChange={(e) => setNotes(e.target.value)} /></label>
        <Button busy={busy === "save"} onClick={() => void save()}>{t("io.save")}</Button>
      </div>
      {preview && <p className="mt-2 text-sm text-ink-soft">{t("io.previewSource", { source: preview.source === "sample" ? t("io.sample") : preview.source })}</p>}
      <section className="mt-8">
        <h2 className="mb-2 text-lg font-bold">{t("io.versions")}</h2>
        <ul className="divide-y divide-line rounded-md border border-line bg-panel text-sm">
          {versions.data?.map((v) => (
            <li key={v.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
              <span><strong>v{v.version}</strong> · {formatDateTime(v.created_at)} · {v.created_by_email ?? t("audit.system")}{v.notes ? ` · ${v.notes}` : ""}</span>
              {v.version === p.current.version ? <Badge tone="good">{t("res.current")}</Badge>
                : <Button variant="ghost" onClick={() => void rollback(v.version)}>{t("io.rollback")}</Button>}
            </li>))}
        </ul>
      </section>
    </div>
  );
}
