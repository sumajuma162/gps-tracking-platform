import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { api, errorMessage } from "../../services/api";
import { useApiQuery, type Page } from "../../services/hooks";
import { Badge, Button, EmptyState, ErrorState, Field, PageHeader, Select, SkeletonRows, TextInput } from "../../components/ui";
import { Dialog } from "../../components/Dialog";
import { toast } from "../../components/Toast";

interface Format { id: number; name: string; slug: string; kind: "json" | "delimited"; definition: Record<string, unknown>; is_active: boolean; notes: string; models_using: string[] }
interface ParseResult { ok: boolean; stage: "format" | "sample"; records?: Record<string, unknown>[]; errors?: { field: string; message: string }[]; ack?: string | null }

export const TEMPLATES: Record<Format["kind"], Record<string, unknown>> = {
  json: { identifier: "device_id", latitude: "lat", longitude: "lon", timestamp: "time", time_format: "iso8601", speed: "speed_kmh",
    speed_unit: "kmh", heading: "heading", fields: { ignition: "ignition" }, sensors: {}, auth: "token", ack: "OK" },
  delimited: { identifier: 0, timestamp: 1, time_format: "unix", latitude: 2, longitude: 3, speed: 4, speed_unit: "kmh",
    fields: { ignition: 5 }, separator: ",", terminator: "\n", ack: "OK" },
};

/** The test-parse box (spec A7): runs a sample through the gateway's parser with the draft definition. */
export function TestParse({ format, draft }: { format: Format; draft: string }) {
  const { t } = useTranslation();
  const [sample, setSample] = useState("");
  const [result, setResult] = useState<ParseResult | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    let definition: unknown;
    try { definition = JSON.parse(draft); } catch { setResult({ ok: false, stage: "format", errors: [{ field: "definition", message: t("formats.badJson") }] }); return; }
    setBusy(true);
    try { setResult((await api.post<ParseResult>(`/device-catalog/generic-formats/${format.id}/test-parse/`, { sample, definition })).data); }
    catch (err) { toast.error(errorMessage(err, t("errors.generic"), t("errors.network"))); }
    finally { setBusy(false); }
  };
  const columns = result?.records?.length ? Object.keys(result.records[0]).filter((k) => k !== "sensors") : [];
  return (
    <section className="space-y-3" aria-label={t("formats.testParse")}>
      <Field id="tp-sample" label={t("formats.sample")} hint={t(format.kind === "json" ? "formats.sampleJsonHint" : "formats.sampleTextHint")}>
        <textarea id="tp-sample" rows={5} value={sample} onChange={(e) => setSample(e.target.value)}
          className="w-full rounded-md border border-line bg-panel px-3 py-2 font-mono text-sm" /></Field>
      <Button type="button" variant="secondary" busy={busy} onClick={() => void run()} disabled={!sample.trim()}>{t("formats.testParse")}</Button>
      {result && (result.ok ? (
        <div className="overflow-x-auto" data-testid="parse-result"><table className="w-full text-left text-xs"><thead><tr>{columns.map((c) => <th key={c} className="px-2 py-1">{c}</th>)}</tr></thead>
          <tbody>{result.records!.map((r, i) => <tr key={i} className="border-t border-line">{columns.map((c) => <td key={c} className="px-2 py-1 font-mono">{String(r[c] ?? "—")}</td>)}</tr>)}</tbody></table>
          <p className="mt-2 text-sm text-ink-soft">{t("formats.parsed", { count: result.records!.length })}</p></div>
      ) : (
        <ul role="alert" className="space-y-1 text-sm text-critical">{result.errors?.map((e, i) => <li key={i}><b>{e.field}</b>: {e.message}</li>)}</ul>))}
    </section>
  );
}

function FormatDialog({ format, onClose, onSaved }: { format: Format | "new"; onClose: () => void; onSaved: () => void }) {
  const { t } = useTranslation();
  const editing = format !== "new";
  const [name, setName] = useState(editing ? format.name : "");
  const [slug, setSlug] = useState(editing ? format.slug : "");
  const [kind, setKind] = useState<Format["kind"]>(editing ? format.kind : "json");
  const [draft, setDraft] = useState(JSON.stringify(editing ? stripKind(format.definition) : TEMPLATES.json, null, 2));
  const [notes, setNotes] = useState(editing ? format.notes : "");
  const [active, setActive] = useState(editing ? format.is_active : true);
  const [errors, setErrors] = useState<string[]>([]);
  const save = async () => {
    let definition: unknown;
    try { definition = JSON.parse(draft); } catch { setErrors([t("formats.badJson")]); return; }
    try {
      const body = { name, slug, kind, definition, notes, is_active: active };
      if (editing) await api.patch(`/device-catalog/generic-formats/${format.id}/`, body);
      else await api.post("/device-catalog/generic-formats/", body);
      toast.success(t("res.saved")); onSaved();
    } catch (err) {
      const details = (err as { response?: { data?: { error?: { details?: Record<string, string[]> } } } }).response?.data?.error?.details;
      setErrors(details ? Object.values(details).flat().map(String) : [errorMessage(err, t("errors.generic"), t("errors.network"))]);
    }
  };
  return (
    <Dialog open wide title={editing ? format.name : t("formats.add")} onClose={onClose}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field id="fmt-name" label={t("common.name")}><TextInput id="fmt-name" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field id="fmt-slug" label={t("formats.slug")} hint={t("formats.slugHint")}><TextInput id="fmt-slug" required pattern="[a-z0-9-]+" maxLength={60} value={slug} onChange={(e) => setSlug(e.target.value)} /></Field>
          <Field id="fmt-kind" label={t("formats.kind")}><Select id="fmt-kind" value={kind} onChange={(e) => { const k = e.target.value as Format["kind"]; setKind(k); if (!editing) setDraft(JSON.stringify(TEMPLATES[k], null, 2)); }}>
            <option value="json">{t("formats.kindJson")}</option><option value="delimited">{t("formats.kindText")}</option></Select></Field>
        </div>
        <Field id="fmt-def" label={t("formats.definition")} hint={t(kind === "json" ? "formats.definitionJsonHint" : "formats.definitionTextHint")}>
          <textarea id="fmt-def" rows={12} value={draft} onChange={(e) => setDraft(e.target.value)} spellCheck={false}
            className="w-full rounded-md border border-line bg-panel px-3 py-2 font-mono text-sm" /></Field>
        <Field id="fmt-notes" label={t("fields.notes")}><TextInput id="fmt-notes" value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
        <label className="flex items-center gap-2 text-sm font-semibold"><input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />{t("common.active")}</label>
        {errors.length > 0 && <ul role="alert" className="text-sm text-critical">{errors.map((e) => <li key={e}>{e}</li>)}</ul>}
        <div className="flex gap-2"><Button type="submit">{t("common.save")}</Button><Button type="button" variant="secondary" onClick={onClose}>{t("common.cancel")}</Button></div>
      </form>
      {editing && <div className="mt-6 border-t border-line pt-4"><TestParse format={{ ...format, kind }} draft={draft} /></div>}
    </Dialog>
  );
}

function stripKind(definition: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(definition).filter(([k]) => k !== "kind" && k !== "slug"));
}

/** UI-defined tracker formats for JSON-over-HTTP and delimited-text trackers (spec A7). Super Admin only. */
export function GenericFormatsPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useApiQuery<Page<Format>>("/device-catalog/generic-formats/");
  const [editing, setEditing] = useState<Format | "new" | null>(null);
  const refresh = () => void qc.invalidateQueries({ predicate: (k) => String(k.queryKey[1]).startsWith("/device-catalog/generic-formats/") });
  return (
    <>
      <PageHeader title={t("formats.title")} lede={t("formats.lede")} actions={<Button onClick={() => setEditing("new")}>{t("formats.add")}</Button>} />
      {q.isLoading ? <SkeletonRows /> : q.isError ? <ErrorState message={t("errors.generic")} onRetry={() => void q.refetch()} />
        : !q.data?.results.length ? <EmptyState message={t("formats.empty")} /> : (
          <ul className="space-y-3">{q.data.results.map((f) => (
            <li key={f.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-line bg-panel p-4">
              <div><div className="font-semibold">{f.name} {!f.is_active && <Badge>{t("common.inactive")}</Badge>}</div>
                <div className="text-sm text-ink-soft"><span className="font-mono">{f.kind === "json" ? `POST /${f.slug}` : f.slug}</span> · {t(f.kind === "json" ? "formats.kindJson" : "formats.kindText")}
                  {f.models_using.length > 0 && <> · {f.models_using.join(", ")}</>}</div></div>
              <Button variant="secondary" onClick={() => setEditing(f)}>{t("formats.editTest")}</Button>
            </li>))}</ul>)}
      {editing && <FormatDialog format={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); refresh(); }} />}
    </>
  );
}
