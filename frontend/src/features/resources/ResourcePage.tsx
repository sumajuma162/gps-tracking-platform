import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { api, errorMessage } from "../../services/api";
import { useApiQuery, type Page } from "../../services/hooks";
import { hasPermission, useAuth } from "../../store/auth";
import { Button, EmptyState, ErrorState, PageHeader, Select, SkeletonRows, TextInput } from "../../components/ui";
import { Dialog } from "../../components/Dialog";
import { Menu } from "../../components/Menu";
import { toast } from "../../components/Toast";
import { ResourceForm } from "./ResourceForm";
import type { ResourceConfig, Row } from "./types";

export function usePermission(perm: string | "platform"): boolean {
  const auth = useAuth();
  return perm === "platform" ? !!auth.user?.is_platform_admin : hasPermission(auth, perm);
}

export function ResourcePage({ config }: { config: ResourceConfig }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const orgId = useAuth((s) => s.activeOrgId);
  const canManage = usePermission(config.managePermission);
  const [search, setSearch] = useState("");
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<Row | null | "new">(null);
  const [deleting, setDeleting] = useState<Row | null>(null);
  const params = { search: search || undefined, page, ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)) };
  const q = useApiQuery<Page<Row>>(config.path, params);
  const refresh = () => void qc.invalidateQueries({ predicate: (query) => query.queryKey[0] === orgId && String(query.queryKey[1]).startsWith(config.path) });
  const helpers = { refresh, canManage };

  const remove = async (row: Row) => {
    try { await api.delete(`${config.path}${row.id}/`); toast.success(t("res.deleted")); refresh(); }
    catch (err) { toast.error(errorMessage(err, t("errors.generic"), t("errors.network"))); }
    setDeleting(null);
  };
  const primary = config.columns.find((c) => c.primary) ?? config.columns[0];
  const rest = config.columns.filter((c) => c !== primary);
  const actions = (row: Row) => (
    <div className="flex items-center justify-end gap-0.5 whitespace-nowrap [&_button]:px-2.5 [&_button]:py-1.5">
      {config.rowActions?.(row, helpers)}
      {canManage && (
        <Menu label={t("res.moreActions", { what: config.describe(row) })} items={[
          { label: t("res.edit"), onSelect: () => setEditing(row) },
          ...(config.canDelete !== false ? [{ label: t("res.delete"), onSelect: () => setDeleting(row), danger: true }] : []),
        ]} />)}
    </div>
  );

  return (
    <>
      <PageHeader title={t(config.title)} lede={t(config.lede)} actions={
        <div className="flex flex-wrap gap-2">
          {config.headerActions?.(helpers)}
          {canManage && config.canCreate !== false && <Button onClick={() => setEditing("new")}>{t("res.add")}</Button>}
        </div>} />
      <div className="mb-4 flex flex-wrap items-end gap-3">
        {config.searchable !== false && <label className="w-72"><span className="sr-only">{t("common.search")}</span>
          <TextInput id={`${config.key}-search`} placeholder={t("common.search")} value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} />
        </label>}
        {config.filters?.map((f) => (
          <label key={f.param} className="w-48"><span className="sr-only">{t(f.label)}</span>
            <Select aria-label={t(f.label)} value={filters[f.param] ?? ""} onChange={(e) => { setFilters({ ...filters, [f.param]: e.target.value }); setPage(1); }}>
              <option value="">{t(f.label)}: {t("res.all")}</option>
              {f.choices.map((c) => <option key={c.value} value={c.value}>{t(c.label)}</option>)}
            </Select>
          </label>
        ))}
      </div>
      {q.isLoading ? <SkeletonRows /> : q.isError ? (
        <ErrorState message={errorMessage(q.error, t("errors.generic"), t("errors.network"))} onRetry={() => void q.refetch()} />
      ) : !q.data?.results.length ? <EmptyState message={t(config.empty)} /> : (
        <>
          <ul className="space-y-3 md:hidden">{q.data.results.map((row) => (
            <li key={row.id} className="space-y-2 rounded-md border border-line bg-panel p-4">
              <div className="font-medium">{primary.render(row)}</div>
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                {rest.map((c) => <div key={c.key} className="contents"><dt className="text-ink-soft">{t(c.header)}</dt><dd className="min-w-0 break-words">{c.render(row)}</dd></div>)}
              </dl>
              {actions(row)}
            </li>))}
          </ul>
          <div className="hidden overflow-x-auto rounded-md border border-line bg-panel md:block">
            <table className="w-full min-w-[48rem] text-left text-sm">
              <thead className="border-b border-line text-ink-soft"><tr>
                {config.columns.map((c) => <th key={c.key} className="px-4 py-3 font-semibold">{t(c.header)}</th>)}
                <th className="px-4 py-3"><span className="sr-only">{t("res.actions")}</span></th>
              </tr></thead>
              <tbody>{q.data.results.map((row) => (
                <tr key={row.id} className="border-b border-line align-top last:border-0">
                  {config.columns.map((c) => <td key={c.key} className={`px-4 py-3 ${c.nowrap ? "whitespace-nowrap" : ""}`}>{c.render(row)}</td>)}
                  <td className="w-px px-3 py-2">{actions(row)}</td>
                </tr>))}</tbody>
            </table>
          </div>
          {(q.data.next || q.data.previous) && (
            <div className="mt-4 flex items-center gap-2 text-sm">
              <Button variant="secondary" disabled={!q.data.previous} onClick={() => setPage((p) => p - 1)}>{t("res.previous")}</Button>
              <Button variant="secondary" disabled={!q.data.next} onClick={() => setPage((p) => p + 1)}>{t("res.next")}</Button>
              <span className="text-ink-soft">{t("res.total", { count: q.data.count })}</span>
            </div>
          )}
        </>
      )}
      {editing !== null && (
        <Dialog open title={editing === "new" ? t("res.addTitle", { what: t(config.title) }) : t("res.editTitle", { what: config.describe(editing) })}
          onClose={() => setEditing(null)} wide>
          <ResourceForm config={config} row={editing === "new" ? null : editing}
            onCancel={() => setEditing(null)}
            onDone={() => { toast.success(editing === "new" ? t("res.created") : t("res.saved")); setEditing(null); refresh(); }} />
        </Dialog>
      )}
      <Dialog open={!!deleting} title={t("res.delete")} onClose={() => setDeleting(null)}>
        <p>{t("res.deleteConfirm", { what: deleting ? config.describe(deleting) : "" })}</p>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setDeleting(null)}>{t("common.cancel")}</Button>
          <Button variant="danger" onClick={() => deleting && void remove(deleting)}>{t("res.delete")}</Button>
        </div>
      </Dialog>
    </>
  );
}
