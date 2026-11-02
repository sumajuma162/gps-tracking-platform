import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useApiQuery } from "../../services/hooks";
import { errorMessage } from "../../services/api";
import { EmptyState, ErrorState, PageHeader, Select, SkeletonRows } from "../../components/ui";

export interface Day { day: string; distance_km: number; trips: number; driving_h: number; idle_h: number; utilization_pct: number;
  overspeed: number; overspeed_critical: number; idle_events: number; idle_minutes: number; alerts_info: number; alerts_warning: number;
  alerts_critical: number; records: number; late_records: number; uptime_pct: number }
interface Body { days: Day[]; status_distribution: Record<string, number>; vehicles: number }

function Chart({ title, children }: { title: string; children: React.ReactElement }) {
  return (
    <section className="rounded-md border border-line bg-panel p-3" aria-label={title}>
      <h2 className="mb-2 font-bold">{title}</h2>
      <div className="h-56"><ResponsiveContainer width="100%" height="100%">{children}</ResponsiveContainer></div>
    </section>
  );
}

const short = (d: string) => d.slice(5);

export function AnalyticsPage() {
  const { t } = useTranslation();
  const [days, setDays] = useState(14);
  const start = useMemo(() => {
    const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() - (days - 1)); return d.toISOString();
  }, [days]);
  const q = useApiQuery<Body>("/analytics/", { start });
  const data = q.data?.days ?? [];
  const empty = data.every((d) => !d.trips && !d.records && !d.alerts_info && !d.alerts_warning && !d.alerts_critical);
  const status = Object.entries(q.data?.status_distribution ?? {}).map(([k, n]) => ({ status: t(`status.${k}`, { defaultValue: k }), n }));
  return (
    <>
      <PageHeader title={t("nav.analytics")} lede={t("analytics.lede")} actions={
        <label className="w-40"><span className="sr-only">{t("analytics.period")}</span>
          <Select aria-label={t("analytics.period")} value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[7, 14, 30, 90].map((n) => <option key={n} value={n}>{t(`analytics.days${n}`)}</option>)}</Select></label>} />
      {q.isLoading ? <SkeletonRows rows={6} /> : q.isError ? <ErrorState message={errorMessage(q.error, t("errors.generic"), t("errors.network"))} onRetry={() => void q.refetch()} /> : (
        <>
          {empty && <EmptyState message={t("analytics.empty")} />}
          <div className="grid gap-4 lg:grid-cols-2" data-testid="analytics-charts">
            <Chart title={t("analytics.distance")}><BarChart data={data}><CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="day" tickFormatter={short} /><YAxis /><Tooltip /><Bar dataKey="distance_km" fill="#1f6f8b" /></BarChart></Chart>
            <Chart title={t("analytics.trips")}><BarChart data={data}><XAxis dataKey="day" tickFormatter={short} /><YAxis allowDecimals={false} /><Tooltip /><Bar dataKey="trips" fill="#3a7d44" /></BarChart></Chart>
            <Chart title={t("analytics.utilization")}><LineChart data={data}><XAxis dataKey="day" tickFormatter={short} /><YAxis unit="%" /><Tooltip /><Line dataKey="utilization_pct" stroke="#1f6f8b" dot={false} /></LineChart></Chart>
            <Chart title={t("analytics.status")}><BarChart data={status} layout="vertical"><XAxis type="number" allowDecimals={false} /><YAxis type="category" dataKey="status" width={90} /><Tooltip /><Bar dataKey="n" fill="#6b5b95" /></BarChart></Chart>
            <Chart title={t("analytics.alerts")}><BarChart data={data}><XAxis dataKey="day" tickFormatter={short} /><YAxis allowDecimals={false} /><Tooltip /><Legend />
              <Bar dataKey="alerts_info" stackId="a" fill="#8aa1b1" name={t("alerts.sev.info")} /><Bar dataKey="alerts_warning" stackId="a" fill="#d9a21b" name={t("alerts.sev.warning")} /><Bar dataKey="alerts_critical" stackId="a" fill="#b3261e" name={t("alerts.sev.critical")} /></BarChart></Chart>
            <Chart title={t("analytics.overspeed")}><BarChart data={data}><XAxis dataKey="day" tickFormatter={short} /><YAxis allowDecimals={false} /><Tooltip /><Bar dataKey="overspeed" fill="#d9a21b" /></BarChart></Chart>
            <Chart title={t("analytics.idle")}><BarChart data={data}><XAxis dataKey="day" tickFormatter={short} /><YAxis /><Tooltip /><Bar dataKey="idle_minutes" fill="#8aa1b1" /></BarChart></Chart>
            <Chart title={t("analytics.uptime")}><LineChart data={data}><XAxis dataKey="day" tickFormatter={short} /><YAxis unit="%" domain={[0, 100]} /><Tooltip /><Line dataKey="uptime_pct" stroke="#3a7d44" dot={false} /></LineChart></Chart>
            <Chart title={t("analytics.ingestion")}><BarChart data={data}><XAxis dataKey="day" tickFormatter={short} /><YAxis /><Tooltip /><Bar dataKey="records" fill="#1f6f8b" /></BarChart></Chart>
          </div>
        </>)}
    </>
  );
}
