import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { LanguageSwitch } from "../components/LanguageSwitch";

export function BrandMark() {
  return (
    <span className="flex items-center gap-2 font-display text-lg font-bold">
      <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden>
        <rect width="26" height="26" rx="5" fill="#17323a" />
        <path d="M6 19 C 10 19, 11 12, 15 12 S 20 7, 21 6" fill="none" stroke="#e8a317" strokeWidth="2.5" strokeDasharray="3 2.5" strokeLinecap="round" />
      </svg>
      Fleet tracking
    </span>
  );
}

export function PublicLayout({ children, nav = true }: { children: ReactNode; nav?: boolean }) {
  const { t } = useTranslation();
  return (
    <div className="flex min-h-screen flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:rounded focus:bg-panel focus:px-3 focus:py-2">Skip to content</a>
      <header className="border-b border-line bg-panel">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-5 py-3">
          <Link to="/" className="mr-auto"><BrandMark /></Link>
          {nav && (
            <nav aria-label="Main" className="flex flex-wrap gap-x-5 text-sm font-medium text-ink-soft">
              <a href="/#features" className="hover:text-ink">{t("landing.navFeatures")}</a>
              <a href="/#devices" className="hover:text-ink">{t("landing.navDevices")}</a>
              <a href="/#how" className="hover:text-ink">{t("landing.navHow")}</a>
              <a href="/#about" className="hover:text-ink">{t("landing.navAbout")}</a>
            </nav>
          )}
          <LanguageSwitch />
          <Link to="/login" className="rounded-md bg-ink px-4 py-2 text-sm font-semibold text-white hover:bg-ink-deep">{t("common.signIn")}</Link>
        </div>
      </header>
      <main id="main" className="flex-1">{children}</main>
      <footer className="border-t border-line bg-panel">
        <div className="mx-auto max-w-6xl px-5 py-6 text-sm text-ink-soft">© {new Date().getFullYear()} {t("landing.footer")}</div>
      </footer>
    </div>
  );
}
