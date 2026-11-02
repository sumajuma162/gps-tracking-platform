import { useState, type ChangeEvent } from "react";
import { useTranslation } from "react-i18next";
import { api, errorMessage, fieldErrors } from "../../services/api";
import { useApiQuery, type Page } from "../../services/hooks";
import { Badge, Button, ErrorState, Field, Select, SkeletonRows, TextInput, formatDateTime } from "../../components/ui";
import { Dialog } from "../../components/Dialog";
import { Markdown } from "../../components/Markdown";
import { toast } from "../../components/Toast";
import type { Row } from "../resources/types";

/** Pick a row from a list endpoint and POST {<field>: id} to an action URL. */
export function AssignDialog({ title, url, field, source, label, onClose, onDone }: {
  title: string; url: string; field: string; source: string; label: (r: Row) => string; onClose: () => void; onDone: () => void;
}) {
  const { t } = useTranslation();
  const q = useApiQuery<Page<Row>>(source, { page_size: 200 });
  const [choice, setChoice] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    if (!choice) { setError(t("common.required")); return; }
    setBusy(true);
    try { await api.post(url, { [field]: Number(choice) }); toast.success(t("res.saved")); onDone(); }
    catch (err) { setError(errorMessage(err, t("errors.generic"), t("errors.network"))); }
    finally { setBusy(false); }
  };
  return (
    <Dialog open title={title} onClose={onClose}>
      <Field id="assign-choice" label={title} error={error ?? undefined}>
        <Select id="assign-choice" value={choice} onChange={(e) => { setChoice(e.target.value); setError(null); }} disabled={q.isLoading}>
          <option value="">{t("res.choose")}</option>
          {q.data?.results.map((r) => <option key={r.id} value={r.id}>{label(r)}</option>)}
        </Select>
      </Field>
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>{t("common.cancel")}</Button>
        <Button busy={busy} onClick={() => void submit()}>{t("res.assign")}</Button>
      </div>
    </Dialog>
  );
}

interface HistoryEntry { name: string; assigned_at: string; unassigned_at: string | null; assigned_by: string | null }
interface History {
  devices: (HistoryEntry & { identifier: string })[];
  drivers: HistoryEntry[];
}

export function HistoryDialog({ vehicle, onClose }: { vehicle: Row; onClose: () => void }) {
  const { t } = useTranslation();
  const q = useApiQuery<History>(`/vehicles/${vehicle.id}/history/`);
  const block = (title: string, rows: HistoryEntry[]) => (
    <section>
      <h3 className="mb-2 font-bold">{title}</h3>
      {rows.length === 0 ? <p className="text-sm text-ink-soft">{t("res.noHistory")}</p> : (
        <ul className="divide-y divide-line rounded-md border border-line text-sm">
          {rows.map((r, i) => (
            <li key={i} className="flex flex-wrap justify-between gap-2 px-3 py-2">
              <span className="font-medium">{r.name}</span>
              <span className="text-ink-soft">
                {formatDateTime(r.assigned_at)} – {r.unassigned_at ? formatDateTime(r.unassigned_at) : <Badge tone="good">{t("res.current")}</Badge>}
              </span>
            </li>))}
        </ul>)}
    </section>
  );
  return (
    <Dialog open wide title={t("res.historyTitle", { what: String(vehicle.registration_number) })} onClose={onClose}>
      {q.isLoading ? <SkeletonRows rows={3} /> : q.isError ? <ErrorState message={t("errors.generic")} /> : (
        <div className="space-y-5">
          {block(t("nav.devices"), (q.data?.devices ?? []).map((d) => ({ ...d, name: `${d.name} (${d.identifier})` })))}
          {block(t("nav.drivers"), q.data?.drivers ?? [])}
        </div>)}
    </Dialog>
  );
}

interface Setup { markdown: string; server_host: string; port: number; transport: string; apn: string }

export function SetupInstructionsDialog({ device, onClose }: { device: Row; onClose: () => void }) {
  const { t } = useTranslation();
  const q = useApiQuery<Setup>(`/devices/${device.id}/setup-instructions/`);
  return (
    <Dialog open wide title={t("devices.setupTitle", { name: String(device.name) })} onClose={onClose}>
      {q.isLoading ? <SkeletonRows rows={4} /> : q.isError || !q.data ? <ErrorState message={t("errors.generic")} /> : (
        <>
          <dl className="mb-4 grid grid-cols-2 gap-3 rounded-md bg-paper p-3 text-sm sm:grid-cols-4">
            <div><dt className="text-ink-soft">{t("devices.server")}</dt><dd className="break-all font-semibold">{q.data.server_host}</dd></div>
            <div><dt className="text-ink-soft">{t("devices.port")}</dt><dd className="font-semibold">{q.data.port}</dd></div>
            <div><dt className="text-ink-soft">{t("devices.transport")}</dt><dd className="font-semibold uppercase">{q.data.transport}</dd></div>
            <div><dt className="text-ink-soft">APN</dt><dd className="font-semibold">{q.data.apn}</dd></div>
          </dl>
          {q.data.markdown ? <Markdown text={q.data.markdown} /> : <p className="text-ink-soft">{t("devices.noInstructions")}</p>}
        </>)}
    </Dialog>
  );
}

interface ImportRow { line: number; identifier: string; name: string; ok: boolean; errors: string[] }
interface ImportResult { valid: number; invalid: number; created: number; rows: ImportRow[] }

export function ImportDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { t } = useTranslation();
  const [csv, setCsv] = useState("");
  const [preview, setPreview] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (commit: boolean) => {
    setBusy(true); setError(null);
    try {
      const { data } = await api.post<ImportResult>("/devices/import/", { csv, commit });
      if (commit) { toast.success(t("devices.imported", { count: data.created })); onDone(); }
      else setPreview(data);
    } catch (err) { setError(errorMessage(err, t("errors.generic"), t("errors.network"))); }
    finally { setBusy(false); }
  };
  const readFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (file.size > 1_000_000) { setError(t("devices.fileTooLarge")); return; }
    setCsv(await file.text()); setPreview(null);
  };
  return (
    <Dialog open wide title={t("devices.importTitle")} onClose={onClose}>
      <p className="mb-3 text-sm text-ink-soft">{t("devices.importHelp")}</p>
      <code className="mb-3 block overflow-x-auto rounded bg-paper p-2 text-xs">model,identifier,name,serial_number,sim_phone,vehicle_registration</code>
      <div className="space-y-3">
        <input type="file" accept=".csv,text/csv" onChange={(e) => void readFile(e)} aria-label={t("devices.chooseFile")} className="text-sm" />
        <label className="block text-sm font-semibold" htmlFor="import-csv">{t("devices.pasteCsv")}</label>
        <textarea id="import-csv" rows={6} value={csv} onChange={(e) => { setCsv(e.target.value); setPreview(null); }}
          className="w-full rounded-md border border-line bg-panel px-3 py-2 font-mono text-xs outline-none focus:border-ink"
          placeholder={"model,identifier,name\nteltonika-fmc920,356307042441013,Truck 1"} />
        {error && <ErrorState message={error} />}
        {preview && (
          <div>
            <p className="mb-2 font-semibold">{t("devices.previewSummary", { valid: preview.valid, invalid: preview.invalid })}</p>
            <ul className="max-h-64 divide-y divide-line overflow-y-auto rounded-md border border-line text-sm">
              {preview.rows.map((r) => (
                <li key={r.line} className="flex flex-wrap gap-2 px-3 py-2">
                  <span className="w-14 text-ink-soft">{t("devices.line", { n: r.line })}</span>
                  <span className="min-w-0 flex-1 break-all">{r.name || "—"} · {r.identifier}</span>
                  {r.ok ? <Badge tone="good">{t("devices.ok")}</Badge> : <span className="w-full text-critical">{r.errors.join(" ")}</span>}
                </li>))}
            </ul>
          </div>)}
      </div>
      <div className="mt-5 flex flex-wrap justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>{t("common.cancel")}</Button>
        <Button variant="secondary" busy={busy && !preview} disabled={!csv.trim()} onClick={() => void run(false)}>{t("devices.check")}</Button>
        <Button busy={busy && !!preview} disabled={!preview || preview.valid === 0} onClick={() => void run(true)}>
          {t("devices.importValid", { count: preview?.valid ?? 0 })}
        </Button>
      </div>
    </Dialog>
  );
}

export function CloneModelDialog({ model, onClose, onDone }: { model: Row; onClose: () => void; onDone: () => void }) {
  const { t } = useTranslation();
  const [name, setName] = useState(`${String(model.name)} copy`);
  const [slug, setSlug] = useState(`${String(model.slug)}-copy`);
  const [error, setError] = useState<string | null>(null);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true); setError(null); setFields({});
    try { await api.post(`/device-catalog/models/${model.id}/clone/`, { name, slug }); toast.success(t("res.created")); onDone(); }
    catch (err) {
      const f = fieldErrors(err);
      // Show the server's reason under the field it concerns (e.g. "A model with this slug already exists.").
      if (f.name || f.slug) setFields(f);
      else setError(errorMessage(err, t("errors.generic"), t("errors.network")));
    }
    finally { setBusy(false); }
  };
  return (
    <Dialog open title={t("catalog.cloneTitle", { name: String(model.name) })} onClose={onClose}>
      <p className="mb-4 text-sm text-ink-soft">{t("catalog.cloneHelp")}</p>
      {error && <div className="mb-3"><ErrorState message={error} /></div>}
      <div className="space-y-4">
        <Field id="clone-name" label={t("fields.name")} error={fields.name}>
          <TextInput id="clone-name" value={name} invalid={!!fields.name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field id="clone-slug" label={t("fields.slug")} hint={t("catalog.slugHint")} error={fields.slug}>
          <TextInput id="clone-slug" value={slug} invalid={!!fields.slug} onChange={(e) => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"))} />
        </Field>
      </div>
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>{t("common.cancel")}</Button>
        <Button busy={busy} onClick={() => void submit()}>{t("catalog.clone")}</Button>
      </div>
    </Dialog>
  );
}
