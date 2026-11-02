import { useTranslation } from "react-i18next";
import { LANGUAGES, setLanguage, type Language } from "../i18n";
import { api } from "../services/api";
import { useAuth } from "../store/auth";

export function LanguageSwitch() {
  const { t, i18n } = useTranslation();
  const signedIn = useAuth((s) => s.status === "authenticated");
  const change = (lang: Language) => {
    setLanguage(lang);
    if (signedIn) void api.patch("/auth/me/", { language: lang }).catch(() => undefined);
  };
  return (
    <label className="flex items-center gap-1 text-sm">
      <span className="sr-only">{t("common.language")}</span>
      <select value={i18n.language} onChange={(e) => change(e.target.value as Language)}
        className="rounded border border-line bg-panel px-2 py-1 text-sm">
        {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
      </select>
    </label>
  );
}
