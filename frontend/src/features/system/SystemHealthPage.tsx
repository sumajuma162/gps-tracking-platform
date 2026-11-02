import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useApiQuery } from "../../services/hooks";
import { useAuth } from "../../store/auth";
import { Badge, ErrorState, PageHeader, SkeletonRows, formatDateTime } from "../../components/ui";

type Check = Record<string, unknown> & { status?: string };
interface Health { status: string; down: string[]; checked_at: string; checks: Record<string, Check> }

/** Platform health for Super Admins: services, queues, devices and ingestion. */
export function SystemHealthPage() {
  const { t } = useTranslation();
  const isPlatformAdmin = useAuth((state) => !!state.user?.is_platform_admin);
  const health = useApiQuery<Health>("/system/health/", undefined, { refetchInterval: 15_000 });
  return (
    <>
      <PageHeader title={t("nav.system")} lede={t("system.lede")}
        actions={isPlatformAdmin ? <Link to="/app/manual-position" className="rounded-md bg-ink px-4 py-2 text-sm font-semibold text-white hover:bg-ink-deep">{t("manualPosition.title")}</Link> : undefined} />
      {health.isLoading ? <SkeletonRows rows={4} /> : health.isError || !health.data ? <ErrorState message={t("errors.generic")} /> : (
        <>
          <p className="mb-4" data-testid="system-status"><Badge tone={health.data.status === "ok" ? "good" : "bad"}>{t(`system.status.${health.data.status}`)}</Badge>
            <span className="ml-2 text-sm text-ink-soft">{formatDateTime(health.data.checked_at)}</span></p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Object.entries(health.data.checks).map(([name, check]) => (
              <section key={name} className="rounded-lg border border-line bg-panel p-3" data-check={name}>
                <h2 className="mb-2 flex items-center justify-between font-bold">{t(`system.check.${name}`, { defaultValue: name })}
                  {check.status && <Badge tone={check.status === "ok" ? "good" : check.status === "not_configured" ? "neutral" : "bad"}>{String(check.status)}</Badge>}</h2>
                <dl className="space-y-0.5 text-sm">{Object.entries(check).filter(([k]) => k !== "status").map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-2"><dt className="text-ink-soft">{k}</dt>
                    <dd className="min-w-0 truncate font-mono text-xs">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd></div>))}</dl>
              </section>))}
          </div>
        </>)}
    </>
  );
}
