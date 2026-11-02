import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useApiQuery, type Page } from "../services/hooks";
import { errorMessage } from "../services/api";
import { activeMembership, useAuth } from "../store/auth";
import { Button, EmptyState, ErrorState, PageHeader, Select, SkeletonRows, TextInput, formatDateTime } from "../components/ui";

interface Entry { id: number; created_at: string; action: string; user_email: string | null; resource_type: string; resource_id: string; ip_address: string | null; user_agent?: string; metadata: Record<string, unknown> }
interface Facets { actions: string[]; resource_types: string[] }
const EMPTY = { q: "", action: "", user: "", resource_type: "", resource_id: "", ip: "", since: "", until: "" };

/** Append-only audit log with search (spec A13): text, action, user, resource, IP and time range. */
export function AuditLogPage() {
  const { t } = useTranslation();
  const membership = activeMembership(useAuth());
  const tz = membership?.organization.timezone;
  const [f, setF] = useState(EMPTY);
  const [page, setPage] = useState(1);
  const params = Object.fromEntries(Object.entries({ ...f,
    since: f.since ? new Date(`${f.since}T00:00:00`).toISOString() : "",
    until: f.until ? new Date(`${f.until}T23:59:59`).toISOString() : "" }).filter(([, v]) => v));
  const q = useApiQuery<Page<Entry>>("/audit-logs/", { ...params, page });
  const facets = useApiQuery<Facets>("/audit-logs/facets/");
  const set = (k: keyof typeof EMPTY) => (e: { target: { value: string } }) => { setF({ ...f, [k]: e.target.value }); setPage(1); };
  return (
    <>
      <PageHeader title={t("audit.title")} lede={t("audit.lede", { org: membership?.organization.name ?? "" })} />
      <div className="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <label><span className="sr-only">{t("auditSearch.text")}</span><TextInput id="audit-q" placeholder={t("auditSearch.text")} value={f.q} onChange={set("q")} /></label>
        <label><span className="sr-only">{t("audit.filterAction")}</span>
          <TextInput id="audit-filter" list="audit-actions" placeholder={t("audit.filterAction")} value={f.action} onChange={set("action")} />
          <datalist id="audit-actions">{facets.data?.actions.map((a) => <option key={a} value={a} />)}</datalist></label>
        <label><span className="sr-only">{t("audit.who")}</span><TextInput id="audit-user" placeholder={t("audit.who")} value={f.user} onChange={set("user")} /></label>
        <label><span className="sr-only">{t("auditSearch.resource")}</span>
          <Select aria-label={t("auditSearch.resource")} value={f.resource_type} onChange={set("resource_type")}>
            <option value="">{t("auditSearch.allResources")}</option>
            {facets.data?.resource_types.map((r) => <option key={r} value={r}>{r}</option>)}</Select></label>
        <label><span className="sr-only">{t("auditSearch.resourceId")}</span><TextInput id="audit-rid" placeholder={t("auditSearch.resourceId")} value={f.resource_id} onChange={set("resource_id")} /></label>
        <label><span className="sr-only">{t("auditSearch.ip")}</span><TextInput id="audit-ip" placeholder={t("auditSearch.ip")} value={f.ip} onChange={set("ip")} /></label>
        <label className="flex items-center gap-2 text-sm">{t("auditSearch.from")}<TextInput id="audit-since" type="date" value={f.since} onChange={set("since")} /></label>
        <label className="flex items-center gap-2 text-sm">{t("auditSearch.to")}<TextInput id="audit-until" type="date" value={f.until} onChange={set("until")} /></label>
      </div>
      {Object.values(f).some(Boolean) && <Button variant="ghost" className="mb-3" onClick={() => { setF(EMPTY); setPage(1); }}>{t("auditSearch.clear")}</Button>}
      {q.isLoading ? <SkeletonRows /> : q.isError ? (
        <ErrorState message={errorMessage(q.error, t("errors.generic"), t("errors.network"))} onRetry={() => void q.refetch()} />
      ) : !q.data?.results.length ? <EmptyState message={t("audit.empty")} /> : (
        <>
          <div className="overflow-x-auto rounded-md border border-line bg-panel">
            <table className="w-full min-w-[40rem] text-left text-sm">
              <thead className="border-b border-line text-ink-soft"><tr>
                <th className="px-4 py-3">{t("audit.when")}</th><th className="px-4 py-3">{t("audit.who")}</th>
                <th className="px-4 py-3">{t("audit.action")}</th><th className="px-4 py-3">{t("audit.details")}</th>
              </tr></thead>
              <tbody>{q.data.results.map((e) => (
                <tr key={e.id} className="border-b border-line align-top last:border-0">
                  <td className="whitespace-nowrap px-4 py-3">{formatDateTime(e.created_at, tz)}</td>
                  <td className="px-4 py-3">{e.user_email ?? t("audit.system")}<div className="text-ink-soft">{e.ip_address}</div>
                    {e.user_agent && <div className="max-w-[14rem] truncate text-xs text-ink-soft" title={e.user_agent}>{e.user_agent}</div>}</td>
                  <td className="px-4 py-3 font-medium">{e.action}{e.resource_type && <div className="text-xs font-normal text-ink-soft">{e.resource_type} #{e.resource_id}</div>}</td>
                  <td className="max-w-md px-4 py-3 text-ink-soft"><code className="break-words text-xs">{Object.keys(e.metadata).length ? JSON.stringify(e.metadata) : ""}</code></td>
                </tr>))}</tbody>
            </table>
          </div>
          <div className="mt-4 flex gap-2">
            <Button variant="secondary" disabled={!q.data.previous} onClick={() => setPage((p) => p - 1)}>‹</Button>
            <Button variant="secondary" disabled={!q.data.next} onClick={() => setPage((p) => p + 1)}>›</Button>
          </div>
        </>
      )}
    </>
  );
}
