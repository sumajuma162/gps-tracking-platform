import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Search } from "lucide-react";
import { api } from "../../services/api";

interface Hit { id: number; label: string; detail: string }
const TARGET: Record<string, (h: Hit) => string> = {
  vehicles: (h) => `/app/history?vehicle=${h.id}`, devices: () => "/app/devices", drivers: () => "/app/drivers",
  geofences: () => "/app/geofences", users: () => "/app/users" };

/** Header search across the organization (spec Phase 9 global search). */
export function GlobalSearch() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Record<string, Hit[]>>({});
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (q.trim().length < 2) { setResults({}); return; }
    const timer = window.setTimeout(async () => {
      try { const { data } = await api.get<{ results: Record<string, Hit[]> }>("/search/", { params: { q } }); setResults(data.results); setOpen(true); }
      catch { setResults({}); }
    }, 250);
    return () => window.clearTimeout(timer);
  }, [q]);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const sections = Object.entries(results);
  return (
    <div className="relative hidden md:block" ref={box}>
      <label className="flex items-center gap-1.5 rounded-md border border-line bg-panel px-2 py-1">
        <Search className="h-4 w-4 text-ink-soft" aria-hidden />
        <span className="sr-only">{t("search.label")}</span>
        <input value={q} onChange={(e) => setQ(e.target.value)} onFocus={() => setOpen(true)} placeholder={t("search.placeholder")}
          className="w-48 bg-transparent text-sm outline-none" data-testid="global-search" />
      </label>
      {open && q.trim().length >= 2 && (
        <div role="listbox" aria-label={t("search.label")} className="absolute right-0 z-50 mt-1 w-80 rounded-lg border border-line bg-panel shadow-lg">
          {sections.length === 0 ? <p className="p-3 text-sm text-ink-soft">{t("search.none")}</p> : sections.map(([kind, hits]) => (
            <div key={kind}><p className="px-3 pt-2 text-xs font-bold uppercase text-ink-soft">{t(`search.kind.${kind}`)}</p>
              {hits.map((h) => (
                <button key={`${kind}${h.id}`} role="option" aria-selected={false} type="button" className="block w-full px-3 py-1.5 text-left text-sm hover:bg-paper"
                  onClick={() => { setOpen(false); setQ(""); navigate(TARGET[kind]?.(h) ?? "/app"); }}>
                  <span className="font-semibold">{h.label}</span> <span className="text-xs text-ink-soft">{h.detail}</span></button>))}</div>))}
        </div>)}
    </div>
  );
}
