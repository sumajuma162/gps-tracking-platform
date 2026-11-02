import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { en } from "./en";
import { sw } from "./sw";

export const LANGUAGES = [
  { code: "en", label: "English" },
  { code: "sw", label: "Kiswahili" },
] as const;
export type Language = (typeof LANGUAGES)[number]["code"];

const STORAGE_KEY = "gps.language";

function initialLanguage(): Language {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "en" || stored === "sw") return stored;
  } catch { /* storage unavailable */ }
  return navigator.language?.toLowerCase().startsWith("sw") ? "sw" : "en";
}

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, sw: { translation: sw } },
  lng: initialLanguage(),
  fallbackLng: "en",
  interpolation: { escapeValue: false }, // React escapes output
});

export function setLanguage(lang: Language): void {
  void i18n.changeLanguage(lang);
  document.documentElement.lang = lang;
  try { localStorage.setItem(STORAGE_KEY, lang); } catch { /* ignore */ }
}

export default i18n;
