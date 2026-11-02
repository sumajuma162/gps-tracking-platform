import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { api, errorMessage } from "../../services/api";
import { useApiQuery, type Page } from "../../services/hooks";
import { hasPermission, useAuth } from "../../store/auth";
import { Badge, Button, EmptyState, Field, PageHeader, SkeletonRows, formatDateTime } from "../../components/ui";
import { Dialog } from "../../components/Dialog";
import { toast } from "../../components/Toast";
import { alertText } from "./messages";

export interface AlertRow { id: number; type: string; severity: "info" | "warning" | "critical"; message: string;
  message_params: Record<string, unknown>; status: "new" | "acknowledged" | "resolved"; occurrences: number; delayed: boolean;
  occurred_at: string; last_occurred_at: string; vehicle: number | null; vehicle_registration: string | null; rule_name: string | null;
  notes: string; acknowledged_by_email: string | null; resolved_by_email: string | null }

const TONE = { critical: "bad", warning: "warn", info: "neutral" } as const;

export function AlertsPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const auth = useAuth();
  const canAct = hasPermission(auth, "alerts.acknowledge");
  const [status, setStatus] = useState("open");
  const [severity, setSeverity] = useState("");
  const list = useApiQuery<Page<AlertRow>>("/alerts/", { status, severity: severity || undefined, page_size: 50 }, { refetchInterval: 30_000 });
  const [acting, setActing] = useState<{ alert: AlertRow; to: "acknowledge" | "resolve" } | null>(null);
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!acting) return;
    setBusy(true);
    try {
      await api.post(`/alerts/${acting.alert.id}/${acting.to}/`, { notes });
      toast.success(t(acting.to === "acknowledge" ? "alerts.acknowledged" : "alerts.resolvedOk"));
      setActing(null); setNotes("");
      void qc.invalidateQueries({ predicate: (q) => String(q.queryKey[1]).startsWith("/alerts") });
    } catch (err) { toast.error(errorMessage(err, t("errors.generic"), t("errors.network"))); }
    finally { setBusy(false); }
  };
  return (
    <>
      <PageHeader title={t("nav.alerts")} lede={t("alerts.lede")}
        actions={hasPermission(auth, "alerts.manage") ? <Link className="rounded-md border border-line px-3 py-2 text-sm font-semibold hover:bg-paper" to="/app/alert-rules">{t("nav.alertRules")}</Link> : undefined} />
      <div className="mb-4 flex flex-wrap gap-2" role="group" aria-label={t("common.filters")}>
        {(["open", "new", "acknowledged", "resolved", ""] as const).map((s) => (
          <button key={s || "all"} type="button" aria-pressed={status === s} onClick={() => setStatus(s)}
            className={`rounded-full border px-3 py-1 text-sm font-semibold ${status === s ? "border-ink bg-ink text-white" : "border-line bg-panel"}`}>{t(`alerts.status.${s || "all"}`)}</button>))}
        <select aria-label={t("alerts.severity")} value={severity} onChange={(e) => setSeverity(e.target.value)} className="rounded border border-line bg-panel px-2 py-1 text-sm">
          <option value="">{t("alerts.anySeverity")}</option>
          {(["critical", "warning", "info"] as const).map((s) => <option key={s} value={s}>{t(`alerts.sev.${s}`)}</option>)}
        </select>
      </div>
      {list.isLoading ? <SkeletonRows rows={4} /> : !list.data?.results.length ? <EmptyState message={t("alerts.empty")} /> : (
        <ol className="space-y-2" aria-label={t("nav.alerts")}>
          {list.data.results.map((a) => (
            <li key={a.id} className="rounded-lg border border-line bg-panel p-3" data-alert={a.type}>
              <div className="flex flex-wrap items-start gap-2">
                <Badge tone={TONE[a.severity]}>{t(`alerts.sev.${a.severity}`)}</Badge>
                {a.delayed && <Badge tone="warn">{t("alerts.delayed")}</Badge>}
                {a.occurrences > 1 && <Badge tone="neutral">×{a.occurrences}</Badge>}
                <span className="min-w-0 flex-1 font-semibold">{alertText(t, a)}</span>
                <Badge tone={a.status === "resolved" ? "good" : a.status === "acknowledged" ? "neutral" : "warn"}>{t(`alerts.status.${a.status}`)}</Badge>
              </div>
              <p className="mt-1 text-xs text-ink-soft">{formatDateTime(a.last_occurred_at)}{a.rule_name ? ` · ${a.rule_name}` : ""}
                {a.acknowledged_by_email ? ` · ${t("alerts.ackBy", { who: a.acknowledged_by_email })}` : ""}</p>
              {a.notes && <p className="mt-1 whitespace-pre-line text-sm">{a.notes}</p>}
              {canAct && a.status !== "resolved" && (
                <div className="mt-2 flex gap-2">
                  {a.status === "new" && <Button variant="secondary" onClick={() => setActing({ alert: a, to: "acknowledge" })}>{t("alerts.acknowledge")}</Button>}
                  <Button variant="secondary" onClick={() => setActing({ alert: a, to: "resolve" })}>{t("alerts.resolve")}</Button>
                </div>)}
            </li>))}
        </ol>)}
      <Dialog open={!!acting} title={acting ? t(acting.to === "acknowledge" ? "alerts.acknowledge" : "alerts.resolve") : ""} onClose={() => setActing(null)}>
        {acting && <p className="mb-3 text-sm">{alertText(t, acting.alert)}</p>}
        <Field id="alert-notes" label={t("alerts.notes")}>
          <textarea id="alert-notes" value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} className="w-full rounded-md border border-line bg-panel p-2" />
        </Field>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setActing(null)}>{t("common.cancel")}</Button>
          <Button busy={busy} onClick={() => void submit()}>{acting ? t(acting.to === "acknowledge" ? "alerts.acknowledge" : "alerts.resolve") : ""}</Button>
        </div>
      </Dialog>
    </>
  );
}
