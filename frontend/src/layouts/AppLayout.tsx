import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { LogOut, Menu, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useAuth, activeMembership } from "../store/auth";
import { visibleNav } from "../features/navigation/nav";
import { logout } from "../services/auth";
import { LanguageSwitch } from "../components/LanguageSwitch";
import { NotificationBell } from "../features/alerts/NotificationBell";
import { GlobalSearch } from "../features/system/GlobalSearch";
import { BrandMark } from "./PublicLayout";

export function AppLayout() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { user, memberships, activeOrgId, setActiveOrg } = useAuth();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const items = visibleNav({ isPlatformAdmin: !!user?.is_platform_admin, memberships, activeOrgId });
  const membership = activeMembership({ memberships, activeOrgId });

  const signOut = async () => {
    await logout();
    queryClient.clear();
    navigate("/login", { replace: true });
  };
  const switchOrg = (id: number) => {
    setActiveOrg(id);
    queryClient.clear(); // never show one organization's cached data in another
  };

  const sidebar = (
    <nav aria-label={t("nav.overview")} className="flex h-full flex-col gap-1 bg-ink px-2 py-4 text-white/85">
      {items.map(({ key, to, icon: Icon }) => (
        <NavLink key={key} to={to} end={to === "/app"} onClick={() => setMobileOpen(false)}
          className={({ isActive }) => `relative flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium hover:bg-white/10 ${isActive ? "bg-white/10 text-white before:absolute before:inset-y-1 before:left-0 before:w-1 before:rounded before:bg-marking" : ""}`}
          title={collapsed ? t(`nav.${key}`) : undefined}>
          <Icon className="h-5 w-5 shrink-0" aria-hidden />
          <span className={collapsed ? "sr-only" : ""}>{t(`nav.${key}`)}</span>
        </NavLink>
      ))}
      <button onClick={() => setCollapsed((c) => !c)} className="mt-auto hidden items-center gap-3 rounded-md px-3 py-2 text-sm hover:bg-white/10 lg:flex"
        aria-label={collapsed ? t("nav.expand") : t("nav.collapse")}>
        {collapsed ? <PanelLeftOpen className="h-5 w-5" /> : <PanelLeftClose className="h-5 w-5" />}
        <span className={collapsed ? "sr-only" : ""}>{t("nav.collapse")}</span>
      </button>
    </nav>
  );

  return (
    <div className="flex min-h-screen">
      <aside className={`hidden shrink-0 lg:block ${collapsed ? "w-16" : "w-60"}`}>{sidebar}</aside>
      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button className="absolute inset-0 bg-ink/40" aria-label={t("common.close")} onClick={() => setMobileOpen(false)} />
          <aside className="relative h-full w-64">{sidebar}</aside>
        </div>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex flex-wrap items-center gap-3 border-b border-line bg-panel px-4 py-2">
          <button className="rounded p-2 hover:bg-paper lg:hidden" onClick={() => setMobileOpen(true)} aria-label={t("nav.expand")}>
            <Menu className="h-5 w-5" />
          </button>
          <BrandMark />
          <div className="ml-auto flex flex-wrap items-center gap-3">
            {memberships.length > 1 && (
              <label className="flex items-center gap-2 text-sm">
                <span className="text-ink-soft">{t("common.organization")}</span>
                <select value={activeOrgId ?? ""} onChange={(e) => switchOrg(Number(e.target.value))}
                  className="rounded border border-line bg-panel px-2 py-1">
                  {memberships.map((m) => <option key={m.organization.id} value={m.organization.id}>{m.organization.name}</option>)}
                </select>
              </label>
            )}
            {memberships.length === 1 && membership && <span className="text-sm font-medium">{membership.organization.name}</span>}
            <GlobalSearch />
            <NotificationBell />
            <LanguageSwitch />
            <span className="hidden text-sm text-ink-soft sm:inline">{user?.full_name || user?.email}</span>
            <button onClick={signOut} className="flex items-center gap-1 rounded-md px-2 py-1 text-sm font-medium hover:bg-paper">
              <LogOut className="h-4 w-4" aria-hidden />{t("nav.signOut")}
            </button>
          </div>
        </header>
        <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-5 py-8"><Outlet /></main>
      </div>
    </div>
  );
}
