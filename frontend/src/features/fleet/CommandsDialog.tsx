import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { api, errorMessage } from "../../services/api";
import { useApiQuery, type Page } from "../../services/hooks";
import { hasPermission, useAuth } from "../../store/auth";
import { Dialog } from "../../components/Dialog";
import { Badge, Button, formatDateTime } from "../../components/ui";
import { toast } from "../../components/Toast";
import type { Row } from "../resources/types";

interface Template { id: number; name: string; command_text: string; description: string; is_dangerous: boolean; is_verified: boolean }
interface CommandRow { id: number; name: string; command_text: string; status: string; created_at: string; error: string;
  requested_by_email: string | null; responses: { response_text: string; received_at: string }[] }

const TONE: Record<string, "good" | "warn" | "bad" | "neutral"> = { ACKNOWLEDGED: "good", SENT: "neutral", PENDING: "neutral", FAILED: "bad", TIMEOUT: "warn" };

/** Codec 12 commands for one tracker (spec A13): templates of its model, queued until the tracker is connected. */
export function CommandsDialog({ device, onClose }: { device: Row; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const auth = useAuth();
  const canSend = hasPermission(auth, "commands.send");
  const templates = useApiQuery<Page<Template>>("/device-catalog/command-templates/", { device_model: device.device_model, page_size: 50 });
  const history = useApiQuery<Page<CommandRow>>("/commands/", { device: device.id, page_size: 20 }, { refetchInterval: 2000 });
  const [confirming, setConfirming] = useState<Template | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const send = async (tpl: Template, confirm = false) => {
    setBusy(tpl.id);
    try {
      await api.post("/commands/", { device: device.id, template: tpl.id, confirm });
      toast.success(t("commands.queued"));
      setConfirming(null);
      void qc.invalidateQueries({ predicate: (q) => String(q.queryKey[1]).startsWith("/commands") });
    } catch (err) { toast.error(errorMessage(err, t("errors.generic"), t("errors.network"))); }
    finally { setBusy(null); }
  };
  return (
    <Dialog open title={t("commands.title", { name: String(device.name) })} onClose={onClose}>
      <p className="mb-3 text-sm text-ink-soft">{t("commands.help")}</p>
      {canSend && (
        <ul className="mb-4 space-y-2" aria-label={t("commands.templates")}>
          {(templates.data?.results ?? []).map((tpl) => (
            <li key={tpl.id} className="flex items-center gap-2 rounded border border-line p-2">
              <span className="min-w-0 flex-1"><span className="font-semibold">{tpl.name}</span> <code className="text-xs">{tpl.command_text}</code>
                <span className="block text-xs text-ink-soft">{tpl.description}</span></span>
              {tpl.is_dangerous && <Badge tone="bad">{t("commands.dangerous")}</Badge>}
              {!tpl.is_verified && <Badge tone="neutral">{t("commands.unverified")}</Badge>}
              <Button variant="secondary" busy={busy === tpl.id} onClick={() => (tpl.is_dangerous ? setConfirming(tpl) : void send(tpl))}>{t("commands.send")}</Button>
            </li>))}
          {templates.data?.results.length === 0 && <li className="text-sm text-ink-soft">{t("commands.noTemplates")}</li>}
        </ul>)}
      <h3 className="mb-2 font-bold">{t("commands.history")}</h3>
      <ol className="max-h-72 space-y-2 overflow-y-auto text-sm" aria-label={t("commands.history")}>
        {(history.data?.results ?? []).map((c) => (
          <li key={c.id} className="rounded border border-line p-2" data-command-status={c.status}>
            <div className="flex items-center gap-2"><span className="font-semibold">{c.name}</span><Badge tone={TONE[c.status] ?? "neutral"}>{t(`commands.status.${c.status}`)}</Badge>
              <span className="ml-auto text-xs text-ink-soft">{formatDateTime(c.created_at)}</span></div>
            {c.responses.map((r) => <pre key={r.received_at} className="mt-1 whitespace-pre-wrap break-all rounded bg-paper p-1.5 text-xs" data-testid="command-response">{r.response_text}</pre>)}
            {c.error && <p className="mt-1 text-xs text-critical">{c.error}</p>}
          </li>))}
        {history.data?.results.length === 0 && <li className="text-ink-soft">{t("commands.none")}</li>}
      </ol>
      <Dialog open={!!confirming} title={t("commands.confirmTitle")} onClose={() => setConfirming(null)}>
        <p>{t("commands.confirmText", { name: confirming?.name ?? "", device: String(device.name) })}</p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setConfirming(null)}>{t("common.cancel")}</Button>
          <Button variant="danger" busy={busy === confirming?.id} onClick={() => confirming && void send(confirming, true)}>{t("commands.confirmSend")}</Button>
        </div>
      </Dialog>
    </Dialog>
  );
}
