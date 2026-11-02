import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

/** "12 s ago" / "3 min ago", refreshed every 10 seconds. */
export function RelativeTime({ iso }: { iso: string | null }) {
  const { i18n, t } = useTranslation();
  const [, tick] = useState(0);
  useEffect(() => { const id = window.setInterval(() => tick((n) => n + 1), 10_000); return () => window.clearInterval(id); }, []);
  if (!iso) return <>{t("devices.neverConnected")}</>;
  const seconds = Math.round((new Date(iso).getTime() - Date.now()) / 1000);
  const rtf = new Intl.RelativeTimeFormat(i18n.language, { numeric: "auto", style: "short" });
  const abs = Math.abs(seconds);
  const [value, unit]: [number, Intl.RelativeTimeFormatUnit] = abs < 60 ? [seconds, "second"] : abs < 3600 ? [Math.round(seconds / 60), "minute"]
    : abs < 86400 ? [Math.round(seconds / 3600), "hour"] : [Math.round(seconds / 86400), "day"];
  return <time dateTime={iso} title={new Date(iso).toLocaleString()}>{rtf.format(value, unit)}</time>;
}
