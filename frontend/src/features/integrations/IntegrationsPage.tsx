import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { api, errorMessage } from "../../services/api";
import { useApiQuery, type Page } from "../../services/hooks";
import { activeMembership, useAuth } from "../../store/auth";
import { Badge, Button, EmptyState, ErrorState, Field, PageHeader, SkeletonRows, TextInput, formatDateTime } from "../../components/ui";
import { Dialog } from "../../components/Dialog";
import { toast } from "../../components/Toast";

interface Hook { id: number; name: string; url: string; events: string[]; is_active: boolean; consecutive_failures: number; last_status: string; last_delivery_at: string | null }
interface Delivery { id: number; event: string; status: string; attempts: number; response_status: number | null; error: string; created_at: string; next_attempt_at: string | null }
interface Key { id: number; name: string; prefix: string; scopes: string[]; expires_at: string | null; revoked_at: string | null; last_used_at: string | null; active: boolean; created_by_email: string }
const EVENTS = ["alert.created", "trip.completed", "geofence.event", "device.offline"];

/** A secret shown exactly once, with a copy button. */
export function OneTimeSecret({ value, message }: { value: string; message: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  return (
    <div className="space-y-2" role="status">
      <p className="font-semibold">{message}</p>
      <div className="flex gap-2">
        <code data-testid="one-time-secret" className="min-w-0 flex-1 break-all rounded-md border border-line bg-paper p-2 text-sm">{value}</code>
        <Button type="button" variant="secondary" onClick={() => { void navigator.clipboard?.writeText(value); setCopied(true); }}>
          {copied ? t("integrations.copied") : t("integrations.copy")}</Button>
      </div>
    </div>
  );
}

function useRefresh(prefix: string) {
  const qc = useQueryClient();
  return () => void qc.invalidateQueries({ predicate: (k) => String(k.queryKey[1]).startsWith(prefix) });
}

function Webhooks() {
  const { t } = useTranslation();
  const tz = activeMembership(useAuth())?.organization.timezone;
  const q = useApiQuery<Page<Hook>>("/webhooks/");
  const refresh = useRefresh("/webhooks/");
  const [adding, setAdding] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  const [log, setLog] = useState<Hook | null>(null);
  const [form, setForm] = useState({ name: "", url: "", events: ["alert.created"] as string[] });
  const [busy, setBusy] = useState(false);
  const create = async () => {
    setBusy(true);
    try {
      const { data } = await api.post<{ secret: string }>("/webhooks/", form);
      setSecret(data.secret); refresh();
    } catch (err) { toast.error(errorMessage(err, t("errors.generic"), t("errors.network"))); }
    finally { setBusy(false); }
  };
  const act = async (hook: Hook, what: "test" | "toggle" | "delete") => {
    try {
      if (what === "test") {
        const { data } = await api.post<Delivery>(`/webhooks/${hook.id}/test/`);
        (data.status === "succeeded" ? toast.success : toast.error)(t("integrations.tested", { status: data.response_status ?? data.error }));
      } else if (what === "toggle") await api.patch(`/webhooks/${hook.id}/`, { is_active: !hook.is_active });
      else await api.delete(`/webhooks/${hook.id}/`);
      refresh();
    } catch (err) { toast.error(errorMessage(err, t("errors.generic"), t("errors.network"))); }
  };
  return (
    <section>
      <div className="mb-3 flex items-center justify-between gap-2">
        <p className="text-sm text-ink-soft">{t("integrations.signatureHelp")}</p>
        <Button onClick={() => { setAdding(true); setSecret(null); setForm({ name: "", url: "", events: ["alert.created"] }); }}>{t("integrations.addWebhook")}</Button>
      </div>
      {q.isLoading ? <SkeletonRows /> : q.isError ? <ErrorState message={t("errors.generic")} onRetry={() => void q.refetch()} />
        : !q.data?.results.length ? <EmptyState message={t("integrations.emptyHooks")} /> : (
          <ul className="space-y-3">{q.data.results.map((h) => (
            <li key={h.id} className="rounded-md border border-line bg-panel p-4" data-webhook={h.name}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0"><div className="font-semibold">{h.name} {!h.is_active && <Badge tone="warn">{t("integrations.disabled")}</Badge>}</div>
                  <div className="truncate font-mono text-xs text-ink-soft">{h.url}</div>
                  <div className="mt-1 flex flex-wrap gap-1">{h.events.map((e) => <Badge key={e}>{t(`integrations.event.${e}`, { defaultValue: e })}</Badge>)}</div></div>
                <div className="flex flex-wrap gap-1">
                  <Button variant="ghost" onClick={() => void act(h, "test")}>{t("integrations.test")}</Button>
                  <Button variant="ghost" onClick={() => setLog(h)}>{t("integrations.deliveries")}</Button>
                  <Button variant="ghost" onClick={() => void act(h, "toggle")}>{h.is_active ? t("common.inactive") : t("common.active")}</Button>
                  <Button variant="ghost" onClick={() => void act(h, "delete")}>{t("res.delete")}</Button>
                </div>
              </div>
              <p className="mt-2 text-sm text-ink-soft">{t("integrations.lastStatus")}: {h.last_status || "—"} · {formatDateTime(h.last_delivery_at, tz)}
                {h.consecutive_failures > 0 && <> · <span className="text-critical">{t("integrations.failures", { count: h.consecutive_failures })}</span></>}</p>
            </li>))}</ul>)}
      <Dialog open={adding} title={t("integrations.addWebhook")} onClose={() => setAdding(false)}>
        {secret ? (<div className="space-y-4"><OneTimeSecret value={secret} message={t("integrations.secretOnce")} />
          <Button onClick={() => setAdding(false)}>{t("common.close")}</Button></div>) : (
          <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); void create(); }}>
            <Field id="wh-name" label={t("common.name")}><TextInput id="wh-name" required maxLength={100} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
            <Field id="wh-url" label={t("integrations.url")}><TextInput id="wh-url" required type="url" placeholder="https://" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} /></Field>
            <fieldset><legend className="text-sm font-semibold">{t("integrations.events")}</legend>
              {EVENTS.map((ev) => (<label key={ev} className="mt-1 flex items-center gap-2">
                <input type="checkbox" checked={form.events.includes(ev)} onChange={(e) => setForm({ ...form, events: e.target.checked ? [...form.events, ev] : form.events.filter((x) => x !== ev) })} />
                {t(`integrations.event.${ev}`)}</label>))}</fieldset>
            <div className="flex gap-2"><Button type="submit" busy={busy} disabled={!form.events.length}>{t("integrations.addWebhook")}</Button>
              <Button type="button" variant="secondary" onClick={() => setAdding(false)}>{t("common.cancel")}</Button></div>
          </form>)}
      </Dialog>
      {log && <DeliveryLog hook={log} tz={tz} onClose={() => setLog(null)} />}
    </section>
  );
}

function DeliveryLog({ hook, tz, onClose }: { hook: Hook; tz?: string; onClose: () => void }) {
  const { t } = useTranslation();
  const q = useApiQuery<Page<Delivery>>(`/webhooks/${hook.id}/deliveries/`);
  return (
    <Dialog open title={`${t("integrations.deliveries")}: ${hook.name}`} onClose={onClose} wide>
      {q.isLoading ? <SkeletonRows /> : !q.data?.results.length ? <EmptyState message={t("integrations.noDeliveries")} /> : (
        <div className="overflow-x-auto"><table className="w-full text-left text-sm"><tbody>
          {q.data.results.map((d) => (<tr key={d.id} className="border-b border-line last:border-0">
            <td className="py-2 pr-3 whitespace-nowrap">{formatDateTime(d.created_at, tz)}</td>
            <td className="py-2 pr-3">{t(`integrations.event.${d.event}`, { defaultValue: d.event })}</td>
            <td className="py-2 pr-3"><Badge tone={d.status === "succeeded" ? "good" : d.status === "failed" ? "bad" : "warn"}>{d.status}</Badge></td>
            <td className="py-2 pr-3">{d.response_status ?? "—"} · ×{d.attempts}</td>
            <td className="py-2 text-ink-soft">{d.error}</td></tr>))}
        </tbody></table></div>)}
    </Dialog>
  );
}

function ApiKeys() {
  const { t } = useTranslation();
  const auth = useAuth();
  const membership = activeMembership(auth);
  const tz = membership?.organization.timezone;
  const mine = membership?.permissions ?? [];
  const q = useApiQuery<Page<Key>>("/api-keys/");
  const refresh = useRefresh("/api-keys/");
  const [adding, setAdding] = useState(false);
  const [raw, setRaw] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", scopes: [] as string[], days: "" });
  const [revoking, setRevoking] = useState<Key | null>(null);
  const create = async () => {
    try {
      const { data } = await api.post<{ key: string }>("/api-keys/", { name: form.name, scopes: form.scopes,
        expires_in_days: form.days ? Number(form.days) : null });
      setRaw(data.key); refresh();
    } catch (err) { toast.error(errorMessage(err, t("errors.generic"), t("errors.network"))); }
  };
  const revoke = async (k: Key) => {
    try { await api.post(`/api-keys/${k.id}/revoke/`); toast.success(t("integrations.revoked")); refresh(); }
    catch (err) { toast.error(errorMessage(err, t("errors.generic"), t("errors.network"))); }
    setRevoking(null);
  };
  return (
    <section>
      <div className="mb-3 flex justify-end"><Button onClick={() => { setAdding(true); setRaw(null); setForm({ name: "", scopes: [], days: "" }); }}>{t("integrations.addKey")}</Button></div>
      {q.isLoading ? <SkeletonRows /> : !q.data?.results.length ? <EmptyState message={t("integrations.emptyKeys")} /> : (
        <div className="overflow-x-auto rounded-md border border-line bg-panel"><table className="w-full min-w-[40rem] text-left text-sm">
          <thead className="border-b border-line text-ink-soft"><tr><th className="px-4 py-3">{t("common.name")}</th><th className="px-4 py-3">{t("integrations.prefix")}</th>
            <th className="px-4 py-3">{t("integrations.scopes")}</th><th className="px-4 py-3">{t("integrations.lastUsed")}</th><th className="px-4 py-3">{t("common.status")}</th><th /></tr></thead>
          <tbody>{q.data.results.map((k) => (<tr key={k.id} className="border-b border-line last:border-0 align-top">
            <td className="px-4 py-3 font-medium">{k.name}<div className="text-xs text-ink-soft">{k.created_by_email}</div></td>
            <td className="px-4 py-3 font-mono text-xs">{k.prefix}…</td>
            <td className="px-4 py-3 text-xs">{k.scopes.join(", ")}</td>
            <td className="px-4 py-3">{formatDateTime(k.last_used_at, tz, t("common.never"))}</td>
            <td className="px-4 py-3"><Badge tone={k.active ? "good" : "neutral"}>{k.active ? t("common.active") : k.revoked_at ? t("integrations.revoked") : t("common.inactive")}</Badge></td>
            <td className="px-4 py-3 text-right">{k.active && <Button variant="ghost" onClick={() => setRevoking(k)}>{t("integrations.revoke")}</Button>}</td>
          </tr>))}</tbody></table></div>)}
      <Dialog open={adding} title={t("integrations.addKey")} onClose={() => setAdding(false)}>
        {raw ? (<div className="space-y-4"><OneTimeSecret value={raw} message={t("integrations.keyOnce")} />
          <Button onClick={() => setAdding(false)}>{t("common.close")}</Button></div>) : (
          <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); void create(); }}>
            <Field id="key-name" label={t("common.name")}><TextInput id="key-name" required maxLength={100} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
            <Field id="key-days" label={t("integrations.expiresIn")} hint={t("integrations.never")}><TextInput id="key-days" type="number" min={1} max={730} value={form.days} onChange={(e) => setForm({ ...form, days: e.target.value })} /></Field>
            <fieldset className="max-h-56 overflow-y-auto"><legend className="text-sm font-semibold">{t("integrations.scopes")}</legend>
              {mine.filter((p) => p !== "integrations.manage").sort().map((p) => (<label key={p} className="mt-1 flex items-center gap-2 font-mono text-xs">
                <input type="checkbox" checked={form.scopes.includes(p)} onChange={(e) => setForm({ ...form, scopes: e.target.checked ? [...form.scopes, p] : form.scopes.filter((x) => x !== p) })} />{p}</label>))}
            </fieldset>
            <div className="flex gap-2"><Button type="submit" disabled={!form.scopes.length}>{t("integrations.addKey")}</Button>
              <Button type="button" variant="secondary" onClick={() => setAdding(false)}>{t("common.cancel")}</Button></div>
          </form>)}
      </Dialog>
      <Dialog open={revoking !== null} title={t("integrations.revoke")} onClose={() => setRevoking(null)}>
        <p className="mb-4">{t("integrations.revokeConfirm", { name: revoking?.name ?? "" })}</p>
        <div className="flex gap-2"><Button variant="danger" onClick={() => revoking && void revoke(revoking)}>{t("integrations.revoke")}</Button>
          <Button variant="secondary" onClick={() => setRevoking(null)}>{t("common.cancel")}</Button></div>
      </Dialog>
    </section>
  );
}

export function IntegrationsPage() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<"webhooks" | "keys">("webhooks");
  return (
    <>
      <PageHeader title={t("nav.integrations")} lede={t("integrations.lede")} />
      <div role="tablist" className="mb-4 flex gap-2">
        <Button role="tab" aria-selected={tab === "webhooks"} variant={tab === "webhooks" ? "primary" : "secondary"} onClick={() => setTab("webhooks")}>{t("integrations.webhooks")}</Button>
        <Button role="tab" aria-selected={tab === "keys"} variant={tab === "keys" ? "primary" : "secondary"} onClick={() => setTab("keys")}>{t("integrations.apiKeys")}</Button>
      </div>
      {tab === "webhooks" ? <Webhooks /> : <ApiKeys />}
    </>
  );
}
