import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { activeMembership, hasPermission, useAuth } from "../store/auth";
import { DashboardPage } from "../features/live/DashboardPage";
import { EmptyState, PageHeader } from "../components/ui";

export function OverviewPage() {
  const auth = useAuth();
  return hasPermission(auth, "dashboard.view") ? <DashboardPage /> : <Welcome />;
}

function Welcome() {
  const { t } = useTranslation();
  const { user, memberships, activeOrgId } = useAuth();
  const membership = activeMembership({ memberships, activeOrgId });
  const name = user?.full_name || user?.email || "";
  return (
    <>
      <PageHeader title={t("app.welcome", { name })}
        lede={membership ? t("app.overviewLede", { org: membership.organization.name, role: membership.role.name }) : undefined} />
      {!membership && user?.is_platform_admin && (
        <EmptyState message={t("orgs.lede")} action={<Link className="font-semibold underline" to="/app/organizations">{t("nav.organizations")}</Link>} />
      )}
      {membership && (
        <div className="grid gap-6 md:grid-cols-2">
          <section className="rounded-lg border border-line bg-panel p-5">
            <h2 className="text-xl font-bold">{t("app.comingTitle")}</h2>
            <p className="mt-2 text-ink-soft">{t("app.comingBody")}</p>
          </section>
          <section className="rounded-lg border border-line bg-panel p-5">
            <h2 className="text-xl font-bold">{t("app.yourAccess")}</h2>
            <p className="mt-2 text-ink-soft">{t("app.permissionsCount", { count: membership.permissions.length })}</p>
            <p className="mt-1 text-sm text-ink-soft">{membership.organization.timezone}</p>
          </section>
        </div>
      )}
    </>
  );
}
