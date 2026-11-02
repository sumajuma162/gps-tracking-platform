import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useApiQuery, type Page } from "../../services/hooks";
import { hasPermission, useAuth } from "../../store/auth";
import { Badge, formatDateTime } from "../../components/ui";
import { alertText } from "./messages";
import type { AlertRow } from "./AlertsPage";

/** Dashboard: alerts today and the latest open alerts (spec A12 dashboard). */
export function DashboardAlerts() {
  const { t } = useTranslation();
  const allowed = hasPermission(useAuth(), "alerts.view");
  const counts = useApiQuery<{ open: number; critical_open: number; today: number }>("/alerts/counts/", undefined, { enabled: allowed, refetchInterval: 30_000 });
  const recent = useApiQuery<Page<AlertRow>>("/alerts/", { status: "open", page_size: 5 }, { enabled: allowed, refetchInterval: 30_000 });
  if (!allowed) return null;
  return (
    <section className="rounded-lg border border-line bg-panel p-4" aria-labelledby="dash-alerts" data-testid="dashboard-alerts">
      <div className="mb-2 flex items-baseline justify-between">
        <h2 id="dash-alerts" className="font-bold">{t("alerts.recent")}</h2>
        <Link to="/app/alerts" className="text-sm font-semibold underline">{t("notify.allAlerts")}</Link>
      </div>
      <dl className="mb-3 flex gap-6">
        <div><dt className="text-xs text-ink-soft">{t("alerts.today")}</dt><dd className="font-display text-2xl font-bold" data-testid="alerts-today">{counts.data?.today ?? "—"}</dd></div>
        <div><dt className="text-xs text-ink-soft">{t("alerts.status.open")}</dt><dd className="font-display text-2xl font-bold">{counts.data?.open ?? "—"}</dd></div>
      </dl>
      <ul className="space-y-1.5 text-sm">
        {(recent.data?.results ?? []).map((a) => (
          <li key={a.id} className="flex items-center gap-2"><Badge tone={a.severity === "critical" ? "bad" : a.severity === "warning" ? "warn" : "neutral"}>{t(`alerts.sev.${a.severity}`)}</Badge>
            <span className="min-w-0 flex-1 truncate">{alertText(t, a)}</span><span className="text-xs text-ink-soft">{formatDateTime(a.last_occurred_at)}</span></li>))}
        {recent.data?.results.length === 0 && <li className="text-ink-soft">{t("alerts.empty")}</li>}
      </ul>
    </section>
  );
}
