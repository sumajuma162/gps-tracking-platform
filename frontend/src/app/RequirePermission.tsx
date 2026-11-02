import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { hasPermission, useAuth } from "../store/auth";
import { ErrorState } from "../components/ui";

export function RequirePermission({ permission, platform, platformOrPermission, children }: {
  permission?: string; platform?: boolean; platformOrPermission?: string; children: ReactNode;
}) {
  const { t } = useTranslation();
  const auth = useAuth();
  const allowed = platform ? !!auth.user?.is_platform_admin : platformOrPermission ?
    !!auth.user?.is_platform_admin || hasPermission(auth, platformOrPermission) : permission ?
      hasPermission(auth, permission) : true;
  return allowed ? <>{children}</> : <ErrorState message={t("errors.forbidden")} />;
}
