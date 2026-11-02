import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api, errorMessage } from "../../services/api";
import { Dialog } from "../../components/Dialog";
import { Button, Field, Select, TextInput } from "../../components/ui";

/** Create a time-limited public link to one vehicle's live position (spec A13). */
export function ShareDialog({ vehicleId, onClose }: { vehicleId: number; onClose: () => void }) {
  const { t } = useTranslation();
  const [hours, setHours] = useState("24");
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const create = async () => {
    try { const { data } = await api.post<{ url: string }>("/share-links/", { vehicle: vehicleId, hours: Number(hours) }); setUrl(data.url); }
    catch (err) { setError(errorMessage(err, t("errors.generic"), t("errors.network"))); }
  };
  return (
    <Dialog open title={t("share.create")} onClose={onClose}>
      <p className="mb-3 text-sm text-ink-soft">{t("share.help")}</p>
      {error && <p role="alert" className="mb-2 text-sm text-critical">{error}</p>}
      {url ? (
        <Field id="share-url" label={t("share.link")}>
          <div className="flex gap-2"><TextInput id="share-url" readOnly value={url} onFocus={(e) => e.target.select()} />
            <Button variant="secondary" onClick={() => void navigator.clipboard?.writeText(url)}>{t("share.copy")}</Button></div>
        </Field>
      ) : (
        <div className="flex items-end gap-2">
          <Field id="share-hours" label={t("share.valid")}><Select id="share-hours" value={hours} onChange={(e) => setHours(e.target.value)}>
            {["1", "4", "24", "72", "168"].map((h) => <option key={h} value={h}>{t("share.hours", { count: Number(h) })}</option>)}</Select></Field>
          <Button onClick={() => void create()}>{t("share.make")}</Button>
        </div>)}
    </Dialog>
  );
}
