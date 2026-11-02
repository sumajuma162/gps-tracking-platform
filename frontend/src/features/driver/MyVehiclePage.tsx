import { useTranslation } from "react-i18next";
import { useApiQuery } from "../../services/hooks";
import { errorMessage } from "../../services/api";
import { activeMembership, useAuth } from "../../store/auth";
import { Badge, EmptyState, ErrorState, PageHeader, SkeletonRows, formatDateTime } from "../../components/ui";

interface Live { status: string; latitude: number | null; longitude: number | null; speed_kmh: number | null; ignition: boolean | null; device_time: string | null }
interface Body { vehicle: { registration_number: string; make: string; model: string } | null; driver?: string; live: Live | null;
  trips: { id: number; start_time: string; end_time: string; distance_km: number; duration_s: number; max_speed_kmh: number }[] }

/** Driver portal (spec A8): only the vehicle assigned to the signed-in driver, and its trips of the last 7 days. */
export function MyVehiclePage() {
  const { t } = useTranslation();
  const tz = activeMembership(useAuth())?.organization.timezone;
  const q = useApiQuery<Body>("/my-vehicle/");
  if (q.isLoading) return <SkeletonRows rows={4} />;
  if (q.isError || !q.data) return <ErrorState message={errorMessage(q.error, t("errors.generic"), t("errors.network"))} onRetry={() => void q.refetch()} />;
  const { vehicle, live, trips } = q.data;
  return (
    <>
      <PageHeader title={t("driver.title")} lede={t("driver.lede")} />
      {!vehicle ? <EmptyState message={t("driver.none")} /> : (
        <div className="space-y-6">
          <section className="rounded-md border border-line bg-panel p-4" aria-label={vehicle.registration_number}>
            <div className="text-xl font-bold">{vehicle.registration_number}</div>
            <div className="text-ink-soft">{[vehicle.make, vehicle.model].filter(Boolean).join(" ")}{q.data.driver ? ` · ${q.data.driver}` : ""}</div>
            {live ? (
              <dl className="mt-3 grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
                <div><dt className="text-ink-soft">{t("common.status")}</dt><dd><Badge>{t(`status.${live.status}`, { defaultValue: live.status })}</Badge></dd></div>
                <div><dt className="text-ink-soft">{t("driver.speed")}</dt><dd>{live.speed_kmh ?? "—"} km/h</dd></div>
                <div><dt className="text-ink-soft">{t("driver.ignition")}</dt><dd>{live.ignition == null ? "—" : live.ignition ? t("driver.on") : t("driver.off")}</dd></div>
                <div><dt className="text-ink-soft">{t("driver.updated")}</dt><dd>{formatDateTime(live.device_time, tz)}</dd></div>
              </dl>) : <p className="mt-3 text-sm text-ink-soft">{t("driver.noTracker")}</p>}
          </section>
          <section aria-label={t("driver.trips")}>
            <h2 className="mb-2 font-bold">{t("driver.trips")}</h2>
            {!trips.length ? <EmptyState message={t("driver.noTrips")} /> : (
              <div className="overflow-x-auto rounded-md border border-line bg-panel"><table className="w-full text-left text-sm">
                <thead className="border-b border-line text-ink-soft"><tr><th className="px-4 py-2">{t("driver.start")}</th><th className="px-4 py-2">{t("driver.end")}</th>
                  <th className="px-4 py-2">km</th><th className="px-4 py-2">{t("driver.duration")}</th><th className="px-4 py-2">{t("driver.maxSpeed")}</th></tr></thead>
                <tbody>{trips.map((tr) => (<tr key={tr.id} className="border-b border-line last:border-0">
                  <td className="px-4 py-2 whitespace-nowrap">{formatDateTime(tr.start_time, tz)}</td><td className="px-4 py-2 whitespace-nowrap">{formatDateTime(tr.end_time, tz)}</td>
                  <td className="px-4 py-2">{tr.distance_km.toFixed(1)}</td><td className="px-4 py-2">{Math.round(tr.duration_s / 60)} min</td><td className="px-4 py-2">{tr.max_speed_kmh} km/h</td></tr>))}</tbody>
              </table></div>)}
          </section>
        </div>)}
    </>
  );
}
