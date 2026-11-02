import { useTranslation } from "react-i18next";
import { useApiQuery, type Page } from "../services/hooks";
import { errorMessage } from "../services/api";
import { Badge, ErrorState, PageHeader, SkeletonRows } from "../components/ui";

interface Role { id: number; name: string; is_system: boolean; permission_codes: string[] }
interface Perm { codename: string; description: string }

export function RolesPage() {
  const { t } = useTranslation();
  const roles = useApiQuery<Page<Role>>("/roles/");
  const perms = useApiQuery<Perm[]>("/permissions/");
  const describe = (code: string) => perms.data?.find((p) => p.codename === code)?.description ?? code;
  return (
    <>
      <PageHeader title={t("roles.title")} lede={t("roles.lede")} />
      {roles.isLoading ? <SkeletonRows /> : roles.isError ? (
        <ErrorState message={errorMessage(roles.error, t("errors.generic"), t("errors.network"))} onRetry={() => void roles.refetch()} />
      ) : (
        <div className="space-y-3">{roles.data?.results.map((r) => (
          <details key={r.id} className="rounded-md border border-line bg-panel">
            <summary className="flex cursor-pointer items-center gap-3 px-4 py-3 font-semibold">
              {r.name} <Badge tone={r.is_system ? "neutral" : "marking"}>{r.is_system ? t("roles.builtIn") : t("roles.custom")}</Badge>
              <span className="ml-auto text-sm font-normal text-ink-soft">{r.permission_codes.length} {t("roles.permissions").toLowerCase()}</span>
            </summary>
            <ul className="grid gap-1 border-t border-line px-4 py-3 text-sm sm:grid-cols-2">
              {r.permission_codes.map((c) => <li key={c}>{describe(c)}</li>)}
            </ul>
          </details>))}
        </div>
      )}
    </>
  );
}
