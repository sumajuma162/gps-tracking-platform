import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { api, errorMessage } from "../../services/api";
import { useApiQuery, type Page } from "../../services/hooks";
import { Button, EmptyState, ErrorState, Field, PageHeader, Select, SkeletonRows, TextInput, formatDateTime } from "../../components/ui";
import { Dialog } from "../../components/Dialog";
import { toast } from "../../components/Toast";

interface Attempt { id: number; identifier: string; protocol: string; transport: string; port: number; last_peer: string;
  first_seen_at: string; last_seen_at: string; attempts: number; sample_hex: string }
interface Org { id: number; name: string }
interface Model { id: number; manufacturer_name: string; name: string; protocol_name: string }

export function UnregisteredPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useApiQuery<Page<Attempt>>("/platform/unregistered-devices/", undefined, { refetchInterval: 10_000 });
  const [registering, setRegistering] = useState<Attempt | null>(null);
  const refresh = () => void qc.invalidateQueries({ predicate: (x) => String(x.queryKey[1]).startsWith("/platform/unregistered") });
  const dismiss = async (a: Attempt) => {
    try { await api.delete(`/platform/unregistered-devices/${a.id}/`); toast.success(t("inbox.dismissed")); refresh(); }
    catch (err) { toast.error(errorMessage(err, t("errors.generic"), t("errors.network"))); }
  };
  return (
    <>
      <PageHeader title={t("nav.unregistered")} lede={t("inbox.lede")} />
      {q.isLoading ? <SkeletonRows /> : q.isError ? <ErrorState message={errorMessage(q.error, t("errors.generic"), t("errors.network"))} onRetry={() => void q.refetch()} />
        : !q.data?.results.length ? <EmptyState message={t("inbox.empty")} /> : (
          <ul className="space-y-3">
            {q.data.results.map((a) => (
              <li key={a.id} className="flex flex-wrap items-start justify-between gap-3 rounded-md border border-line bg-panel p-4" data-identifier={a.identifier}>
                <div className="min-w-0">
                  <div className="font-mono text-lg font-semibold">{a.identifier}</div>
                  <div className="text-sm text-ink-soft">{t("inbox.summary", { protocol: a.protocol, port: a.port, transport: a.transport.toUpperCase(), count: a.attempts })}</div>
                  <div className="text-sm text-ink-soft">{t("inbox.seen", { first: formatDateTime(a.first_seen_at), last: formatDateTime(a.last_seen_at), peer: a.last_peer })}</div>
                  <code className="mt-1 block break-all text-xs text-ink-soft">{a.sample_hex.slice(0, 64)}</code>
                </div>
                <div className="flex gap-2">
                  <Button variant="secondary" onClick={() => void dismiss(a)}>{t("inbox.dismiss")}</Button>
                  <Button onClick={() => setRegistering(a)}>{t("inbox.register")}</Button>
                </div>
              </li>))}
          </ul>)}
      {registering && <RegisterDialog attempt={registering} onClose={() => setRegistering(null)} onDone={() => { setRegistering(null); refresh(); }} />}
    </>
  );
}

function RegisterDialog({ attempt, onClose, onDone }: { attempt: Attempt; onClose: () => void; onDone: () => void }) {
  const { t } = useTranslation();
  const orgs = useApiQuery<Page<Org>>("/organizations/", { page_size: 200 });
  const models = useApiQuery<Page<Model>>("/device-catalog/models/", { active: 1, page_size: 200 });
  const matching = (models.data?.results ?? []).filter((m) => m.protocol_name === attempt.protocol);
  const [org, setOrg] = useState("");
  const [model, setModel] = useState("");
  const [name, setName] = useState(`Tracker ${attempt.identifier.slice(-4)}`);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!org || !model || !name.trim()) { setError(t("inbox.fillAll")); return; }
    setBusy(true);
    try {
      const { data } = await api.post<{ organization: string }>(`/platform/unregistered-devices/${attempt.id}/register/`,
        { organization: Number(org), device_model: Number(model), name: name.trim() });
      toast.success(t("inbox.registered", { org: data.organization }));
      onDone();
    } catch (err) { setError(errorMessage(err, t("errors.generic"), t("errors.network"))); }
    finally { setBusy(false); }
  };
  return (
    <Dialog open title={t("inbox.registerTitle", { identifier: attempt.identifier })} onClose={onClose}>
      <p className="mb-4 text-sm text-ink-soft">{t("inbox.registerHelp")}</p>
      {error && <div className="mb-3"><ErrorState message={error} /></div>}
      <div className="space-y-4">
        <Field id="reg-org" label={`${t("common.organization")} *`}>
          <Select id="reg-org" value={org} onChange={(e) => setOrg(e.target.value)}>
            <option value="">{t("res.choose")}</option>
            {orgs.data?.results.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </Select></Field>
        <Field id="reg-model" label={`${t("fields.device_model")} *`} hint={t("inbox.modelHint", { protocol: attempt.protocol })}>
          <Select id="reg-model" value={model} onChange={(e) => setModel(e.target.value)}>
            <option value="">{t("res.choose")}</option>
            {matching.map((m) => <option key={m.id} value={m.id}>{m.manufacturer_name} {m.name}</option>)}
          </Select></Field>
        <Field id="reg-name" label={`${t("fields.name")} *`}><TextInput id="reg-name" value={name} onChange={(e) => setName(e.target.value)} /></Field>
      </div>
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>{t("common.cancel")}</Button>
        <Button busy={busy} onClick={() => void submit()}>{t("inbox.register")}</Button>
      </div>
    </Dialog>
  );
}
