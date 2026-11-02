import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { Bell } from "lucide-react";
import { api } from "../../services/api";
import { useApiQuery, type Page } from "../../services/hooks";
import { hasPermission, useAuth } from "../../store/auth";
import { toast } from "../../components/Toast";
import { formatDateTime } from "../../components/ui";

interface Item { id: number; title: string; severity: string; read_at: string | null; created_at: string; alert: number | null }

/** Header bell: unread count (REST + live WebSocket push) and the latest notifications (spec A11). */
export function NotificationBell() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const auth = useAuth();
  const allowed = hasPermission(auth, "notifications.view");
  const unread = useApiQuery<{ unread: number }>("/notifications/unread/", undefined, { enabled: allowed, refetchInterval: 60_000 });
  const [open, setOpen] = useState(false);
  const list = useApiQuery<Page<Item>>("/notifications/", { page_size: 10 }, { enabled: allowed && open });
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {                                   // live push over /ws/notifications/
    if (!allowed) return;
    let ws: WebSocket | null = null;
    let stopped = false;
    let retry: number | undefined;
    const connect = async () => {
      try {
        const { data } = await api.post<{ ticket: string }>("/auth/ws-ticket/");
        if (stopped) return;
        const proto = window.location.protocol === "https:" ? "wss" : "ws";
        ws = new WebSocket(`${proto}://${window.location.host}/ws/notifications/?ticket=${encodeURIComponent(data.ticket)}`);
        ws.onmessage = (e) => {
          const msg = JSON.parse(String(e.data)) as { type: string; notification?: Item };
          if (msg.type !== "notification" || !msg.notification) return;
          void qc.invalidateQueries({ predicate: (q) => String(q.queryKey[1]).startsWith("/notifications") || String(q.queryKey[1]).startsWith("/alerts") });
          (msg.notification.severity === "critical" ? toast.error : toast.success)(msg.notification.title);
        };
        ws.onclose = () => { if (!stopped) retry = window.setTimeout(() => void connect(), 5000); };
      } catch { if (!stopped) retry = window.setTimeout(() => void connect(), 10_000); }
    };
    void connect();
    return () => { stopped = true; window.clearTimeout(retry); ws?.close(); };
  }, [allowed, qc]);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  if (!allowed) return null;
  const count = unread.data?.unread ?? 0;
  const readAll = async () => {
    await api.post("/notifications/read-all/");
    void qc.invalidateQueries({ predicate: (q) => String(q.queryKey[1]).startsWith("/notifications") });
  };
  return (
    <div className="relative" ref={box}>
      <button type="button" aria-label={t("notify.bell", { count })} aria-expanded={open} onClick={() => setOpen((v) => !v)}
        className="relative rounded-md p-1.5 hover:bg-paper" data-testid="notification-bell">
        <Bell className="h-5 w-5" aria-hidden />
        {count > 0 && <span className="absolute -right-1 -top-1 min-w-5 rounded-full bg-critical px-1 text-center text-xs font-bold text-white" data-testid="unread-count">{count > 99 ? "99+" : count}</span>}
      </button>
      {open && (
        <div role="dialog" aria-label={t("notify.title")} className="absolute right-0 z-50 mt-2 w-80 max-w-[90vw] rounded-lg border border-line bg-panel shadow-lg">
          <div className="flex items-center justify-between border-b border-line px-3 py-2">
            <span className="font-bold">{t("notify.title")}</span>
            {count > 0 && <button type="button" className="text-sm font-semibold underline" onClick={() => void readAll()}>{t("notify.readAll")}</button>}
          </div>
          <ul className="max-h-96 overflow-y-auto">
            {(list.data?.results ?? []).map((n) => (
              <li key={n.id} className={`border-b border-line px-3 py-2 text-sm ${n.read_at ? "text-ink-soft" : "font-semibold"}`}>
                <span className={`mr-1.5 inline-block h-2 w-2 rounded-full ${n.severity === "critical" ? "bg-critical" : n.severity === "warning" ? "bg-marking" : "bg-ink-soft"}`} aria-hidden />
                {n.title}<span className="block text-xs font-normal text-ink-soft">{formatDateTime(n.created_at)}</span>
              </li>))}
            {list.data && list.data.results.length === 0 && <li className="px-3 py-4 text-sm text-ink-soft">{t("notify.empty")}</li>}
          </ul>
          <Link to="/app/alerts" onClick={() => setOpen(false)} className="block px-3 py-2 text-center text-sm font-semibold hover:bg-paper">{t("notify.allAlerts")}</Link>
        </div>)}
    </div>
  );
}
