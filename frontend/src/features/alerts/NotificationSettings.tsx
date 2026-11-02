import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errorMessage } from "../../services/api";
import { useApiQuery } from "../../services/hooks";
import { hasPermission, useAuth } from "../../store/auth";
import { Button, Field, Select, TextInput } from "../../components/ui";
import { toast } from "../../components/Toast";

interface Pref { channel: string; alert_type: string; min_severity: string; enabled: boolean }
interface Prefs { preferences: Pref[]; quiet_hours_start: string | null; quiet_hours_end: string | null; time_zone: string }

/** Profile section: per-channel minimum severity and quiet hours (spec A11). */
export function NotificationSettings() {
  const { t } = useTranslation();
  const allowed = hasPermission(useAuth(), "notifications.view");
  const prefs = useApiQuery<Prefs>("/notification-preferences/", undefined, { enabled: allowed });
  const [state, setState] = useState({ in_app: "info", email: "critical", quietFrom: "", quietTo: "", tz: "Africa/Dar_es_Salaam" });
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!prefs.data) return;
    const level = (ch: string, fallback: string) => {
      const p = prefs.data.preferences.find((x) => x.channel === ch && x.alert_type === "");
      return p ? (p.enabled ? p.min_severity : "off") : fallback;
    };
    setState({ in_app: level("in_app", "info"), email: level("email", "critical"), quietFrom: prefs.data.quiet_hours_start?.slice(0, 5) ?? "",
      quietTo: prefs.data.quiet_hours_end?.slice(0, 5) ?? "", tz: prefs.data.time_zone });
  }, [prefs.data]);
  if (!allowed) return null;
  const save = async () => {
    setBusy(true);
    const kept = (prefs.data?.preferences ?? []).filter((p) => p.alert_type !== "");
    const base = (["in_app", "email"] as const).map((ch) => ({ channel: ch, alert_type: "", enabled: state[ch] !== "off", min_severity: state[ch] === "off" ? "info" : state[ch] }));
    try {
      await api.put("/notification-preferences/replace/", { preferences: [...base, ...kept], time_zone: state.tz,
        quiet_hours_start: state.quietFrom || null, quiet_hours_end: state.quietTo || null });
      toast.success(t("res.saved"));
    } catch (err) { toast.error(errorMessage(err, t("errors.generic"), t("errors.network"))); }
    finally { setBusy(false); }
  };
  const levels = ["info", "warning", "critical", "off"];
  return (
    <section className="mt-6 rounded-lg border border-line bg-panel p-4" aria-labelledby="notif-h">
      <h2 id="notif-h" className="mb-1 font-bold">{t("notify.settings")}</h2>
      <p className="mb-3 text-sm text-ink-soft">{t("notify.settingsHelp")}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        {(["in_app", "email"] as const).map((ch) => (
          <Field key={ch} id={`pref-${ch}`} label={t(`notify.channel.${ch}`)}>
            <Select id={`pref-${ch}`} value={state[ch]} onChange={(e) => setState({ ...state, [ch]: e.target.value })}>
              {levels.map((l) => <option key={l} value={l}>{t(`notify.level.${l}`)}</option>)}</Select></Field>))}
        <Field id="quiet-from" label={t("notify.quietFrom")} hint={t("notify.quietHint")}>
          <TextInput id="quiet-from" type="time" value={state.quietFrom} onChange={(e) => setState({ ...state, quietFrom: e.target.value })} /></Field>
        <Field id="quiet-to" label={t("notify.quietTo")}>
          <TextInput id="quiet-to" type="time" value={state.quietTo} onChange={(e) => setState({ ...state, quietTo: e.target.value })} /></Field>
      </div>
      <div className="mt-3 flex justify-end"><Button busy={busy} onClick={() => void save()}>{t("common.save")}</Button></div>
    </section>
  );
}
