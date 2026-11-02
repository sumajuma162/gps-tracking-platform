import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, errorMessage } from "../../services/api";
import { useApiQuery, type Page } from "../../services/hooks";
import { hasPermission, useAuth } from "../../store/auth";
import { Button, EmptyState, ErrorState, Field, PageHeader, Select, SkeletonRows, TextInput } from "../../components/ui";
import { toast } from "../../components/Toast";
import { ResourcePage } from "../resources/ResourcePage";
import type { ResourceConfig } from "../resources/types";

interface Col { key: string; label: string; kind: string }
interface Def { key: string; title: string; columns: Col[]; filters: string[]; chart: [string, string] | null }
interface Result { columns: Col[]; rows: Record<string, unknown>[]; count: number; page: number; page_size: number; chart: { label: string; value: number }[] | null }
interface Job { id: number; status: string; progress: number; downloadable: boolean; filename: string; error: string }

const toLocal = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);

/** Blob text, with a FileReader fallback for environments without Blob.text(). */
function blobText(blob: Blob): Promise<string> {
  if (typeof blob.text === "function") return blob.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

async function save(blob: Blob, name: string) {
  const href = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement("a"), { href, download: name });
  document.body.appendChild(a); a.click(); a.remove();
  window.setTimeout(() => URL.revokeObjectURL(href), 10_000);
}

function Schedules({ defs }: { defs: Def[] }) {
  const config: ResourceConfig = {
    key: "schedules", path: "/scheduled-reports/", title: "reports.schedules", lede: "reports.schedulesLede", empty: "reports.noSchedules",
    viewPermission: "reports.view", managePermission: "reports.export", canDelete: true, describe: (r) => String(r.name),
    columns: [
      { key: "name", header: "fields.name", primary: true, render: (r) => <span className="font-semibold">{String(r.name)}</span> },
      { key: "report", header: "reports.report", render: (r) => defs.find((d) => d.key === r.report)?.title ?? String(r.report) },
      { key: "frequency", header: "reports.frequency", render: (r) => String(r.frequency) },
      { key: "recipients", header: "reports.recipients", render: (r) => (r.recipients as string[]).join(", ") },
    ],
    toForm: (r) => ({ ...r, recipients: (r.recipients as string[]).join(", ") }),
    fromForm: (p) => ({ ...p, recipients: String(p.recipients ?? "").split(/[\s,;]+/).filter(Boolean) }),
    fields: [
      { name: "name", label: "fields.name", required: true },
      { name: "report", label: "reports.report", type: "select", required: true, choices: defs.map((d) => ({ value: d.key, label: d.title })) },
      { name: "frequency", label: "reports.frequency", type: "select", required: true,
        choices: ["daily", "weekly", "monthly"].map((v) => ({ value: v, label: `reports.freq.${v}` })) },
      { name: "format", label: "reports.format", type: "select", required: true, choices: [{ value: "xlsx", label: "Excel" }, { value: "pdf", label: "PDF" }, { value: "csv", label: "CSV" }] },
      { name: "recipients", label: "reports.recipients", required: true, hint: "reports.recipientsHint" },
      { name: "is_active", label: "common.active", type: "checkbox", defaultValue: true },
    ],
  };
  return <ResourcePage config={config} />;
}

export function ReportsPage() {
  const { t } = useTranslation();
  const auth = useAuth();
  const canExport = hasPermission(auth, "reports.export");
  const catalog = useApiQuery<{ reports: Def[] }>("/reports/");
  const vehicles = useApiQuery<Page<{ id: number; registration_number: string }>>("/vehicles/", { page_size: 200 });
  const groups = useApiQuery<Page<{ id: number; name: string }>>("/vehicle-groups/", { page_size: 200 });
  const drivers = useApiQuery<Page<{ id: number; full_name: string }>>("/drivers/", { page_size: 200 });
  const [tab, setTab] = useState<"run" | "schedules">("run");
  const [key, setKey] = useState("trips");
  const now = new Date();
  const [filters, setFilters] = useState({ vehicle: "", group: "", driver: "", status: "", start: toLocal(new Date(now.getTime() - 7 * 86_400_000)), end: toLocal(now) });
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const def = catalog.data?.reports.find((r) => r.key === key);
  const params = () => ({ vehicle: filters.vehicle || undefined, group: filters.group || undefined, driver: filters.driver || undefined,
    status: filters.status || undefined, start: new Date(filters.start).toISOString(), end: new Date(filters.end).toISOString() });

  const run = async (p = 1) => {
    setLoading(true); setError(null);
    try { const { data } = await api.get<Result>(`/reports/${key}/`, { params: { ...params(), page: p, page_size: 50 } }); setResult(data); setPage(p); }
    catch (err) { setError(errorMessage(err, t("errors.generic"), t("errors.network"))); }
    finally { setLoading(false); }
  };
  useEffect(() => { setResult(null); setJob(null); }, [key]);
  useEffect(() => {                                     // background export progress
    if (!job || job.status === "done" || job.status === "failed") return;
    const timer = window.setInterval(async () => {
      try { const { data } = await api.get<Job>(`/report-jobs/${job.id}/`); setJob(data); } catch { /* keep polling */ }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [job]);

  const exportAs = async (format: "csv" | "xlsx" | "pdf") => {
    try {
      const resp = await api.post(`/reports/${key}/export/`, { format, filters: params() }, { responseType: "blob" });
      if (resp.status === 202) { setJob(JSON.parse(await blobText(resp.data as Blob)) as Job); toast.success(t("reports.queued")); return; }
      const name = /filename="([^"]+)"/.exec(String(resp.headers["content-disposition"] ?? ""))?.[1] ?? `${key}.${format}`;
      await save(resp.data as Blob, name);
    } catch (err) { toast.error(errorMessage(err, t("errors.generic"), t("errors.network"))); }
  };
  const downloadJob = async () => {
    if (!job) return;
    const resp = await api.get(`/report-jobs/${job.id}/download/`, { responseType: "blob" });
    await save(resp.data as Blob, job.filename);
  };
  const shows = (f: string) => def?.filters.includes(f);
  return (
    <>
      <PageHeader title={t("nav.reports")} lede={t("reports.lede")} />
      <div className="mb-4 flex gap-2" role="tablist">
        {(["run", "schedules"] as const).map((x) => (
          <button key={x} role="tab" aria-selected={tab === x} onClick={() => setTab(x)}
            className={`rounded-full border px-3 py-1 text-sm font-semibold ${tab === x ? "border-ink bg-ink text-white" : "border-line bg-panel"}`}>{t(`reports.tab.${x}`)}</button>))}
      </div>
      {tab === "schedules" ? <Schedules defs={catalog.data?.reports ?? []} /> : (
        <div className="grid gap-4 lg:grid-cols-[14rem_1fr]">
          <nav aria-label={t("reports.types")}>
            <ul className="space-y-1">{(catalog.data?.reports ?? []).map((r) => (
              <li key={r.key}><button type="button" aria-current={r.key === key} onClick={() => setKey(r.key)}
                className={`w-full rounded px-2 py-1.5 text-left text-sm ${r.key === key ? "bg-ink font-semibold text-white" : "hover:bg-paper"}`}>{t(`reports.title.${r.key}`, { defaultValue: r.title })}</button></li>))}
            </ul>
          </nav>
          <section>
            <form className="mb-3 flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); void run(1); }} aria-label={t("common.filters")}>
              {shows("vehicle") && <div className="w-40"><Field id="r-vehicle" label={t("devices.vehicle")}><Select id="r-vehicle" value={filters.vehicle} onChange={(e) => setFilters({ ...filters, vehicle: e.target.value })}>
                <option value="">{t("res.all")}</option>{vehicles.data?.results.map((v) => <option key={v.id} value={v.id}>{v.registration_number}</option>)}</Select></Field></div>}
              {shows("group") && <div className="w-40"><Field id="r-group" label={t("vehicles.groups")}><Select id="r-group" value={filters.group} onChange={(e) => setFilters({ ...filters, group: e.target.value })}>
                <option value="">{t("res.all")}</option>{groups.data?.results.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}</Select></Field></div>}
              {shows("driver") && <div className="w-40"><Field id="r-driver" label={t("reports.driver")}><Select id="r-driver" value={filters.driver} onChange={(e) => setFilters({ ...filters, driver: e.target.value })}>
                <option value="">{t("res.all")}</option>{drivers.data?.results.map((d) => <option key={d.id} value={d.id}>{d.full_name}</option>)}</Select></Field></div>}
              {shows("status") && <div className="w-36"><Field id="r-status" label={t("common.status")}><Select id="r-status" value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
                <option value="">{t("res.all")}</option>{["open", "new", "acknowledged", "resolved"].map((s) => <option key={s} value={s}>{t(`alerts.status.${s}`)}</option>)}</Select></Field></div>}
              {shows("start") && <><div className="w-48"><Field id="r-start" label={t("history.from")}><TextInput id="r-start" type="datetime-local" value={filters.start} onChange={(e) => setFilters({ ...filters, start: e.target.value })} /></Field></div>
                <div className="w-48"><Field id="r-end" label={t("history.to")}><TextInput id="r-end" type="datetime-local" value={filters.end} onChange={(e) => setFilters({ ...filters, end: e.target.value })} /></Field></div></>}
              <Button type="submit" busy={loading}>{t("reports.run")}</Button>
              {canExport && <div className="flex gap-1" role="group" aria-label={t("reports.export")}>
                {(["csv", "xlsx", "pdf"] as const).map((f) => <Button key={f} type="button" variant="secondary" onClick={() => void exportAs(f)}>{f === "xlsx" ? "Excel" : f.toUpperCase()}</Button>)}</div>}
            </form>
            {job && (
              <div className="mb-3 rounded border border-line bg-panel p-3 text-sm" role="status" data-testid="report-job">
                {job.status === "failed" ? <span className="text-critical">{job.error}</span> : job.downloadable
                  ? <button type="button" className="font-semibold underline" onClick={() => void downloadJob()}>{t("reports.download", { name: job.filename })}</button>
                  : <span>{t("reports.preparing", { progress: job.progress })}</span>}
                {!job.downloadable && job.status !== "failed" && <div className="mt-2 h-2 rounded bg-paper"><div className="h-2 rounded bg-ink" style={{ width: `${job.progress}%` }} /></div>}
              </div>)}
            {error && <ErrorState message={error} />}
            {loading && !result ? <SkeletonRows rows={5} /> : !result ? <EmptyState message={t("reports.choose")} /> : result.count === 0 ? <EmptyState message={t("reports.empty")} /> : (
              <>
                {result.chart && result.chart.length > 0 && (
                  <div className="mb-3 h-48 rounded border border-line bg-panel p-2" role="img" aria-label={t("reports.chart")}>
                    <ResponsiveContainer width="100%" height="100%"><BarChart data={result.chart}><XAxis dataKey="label" tick={{ fontSize: 11 }} /><YAxis tick={{ fontSize: 11 }} width={40} /><Tooltip /><Bar dataKey="value" fill="#14323b" isAnimationActive={false} /></BarChart></ResponsiveContainer>
                  </div>)}
                <div className="overflow-x-auto rounded-lg border border-line bg-panel">
                  <table className="w-full text-sm" data-testid="report-table">
                    <thead><tr className="border-b border-line text-left">{result.columns.map((c) => <th key={c.key} className="whitespace-nowrap px-3 py-2 font-semibold">{t(`reports.col.${c.key}`, { defaultValue: c.label })}</th>)}</tr></thead>
                    <tbody>{result.rows.map((r, i) => <tr key={i} className="border-b border-line last:border-0">{result.columns.map((c) => <td key={c.key} className="whitespace-nowrap px-3 py-1.5">{String(r[c.key] ?? "")}</td>)}</tr>)}</tbody>
                  </table>
                </div>
                <div className="mt-2 flex items-center justify-between text-sm">
                  <span>{t("reports.count", { count: result.count })}</span>
                  <span className="flex gap-2">
                    <Button variant="ghost" disabled={page <= 1} onClick={() => void run(page - 1)}>{t("res.previous")}</Button>
                    <Button variant="ghost" disabled={page * result.page_size >= result.count} onClick={() => void run(page + 1)}>{t("res.next")}</Button>
                  </span>
                </div>
              </>)}
          </section>
        </div>)}
    </>
  );
}
