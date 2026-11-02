import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useApiQuery, type Page } from "../../services/hooks";
import { Badge, formatDateTime } from "../../components/ui";
import { ResourcePage } from "../resources/ResourcePage";
import type { ResourceConfig, Row } from "../resources/types";
import { formatDuration } from "../history/playback";

function ViewRoute({ row }: { row: Row }) {
  const { t } = useTranslation();
  if (!row.vehicle) return null;
  const q = new URLSearchParams({ vehicle: String(row.vehicle), from: String(row.start_time), to: String(row.end_time) });
  return <Link to={`/app/history?${q.toString()}`} className="rounded-md px-2.5 py-1.5 text-sm font-semibold hover:bg-paper">{t("trips.viewRoute")}</Link>;
}

function Translated({ k, opts }: { k: string; opts?: Record<string, unknown> }) {
  const { t } = useTranslation();
  return <>{t(k, opts)}</>;
}

export function TripsPage() {
  const vehicles = useApiQuery<Page<{ id: number; registration_number: string }>>("/vehicles/", { page_size: 200 });
  const config: ResourceConfig = {
    key: "trips", path: "/trips/", title: "nav.trips", lede: "trips.lede", empty: "trips.empty",
    viewPermission: "trips.view", managePermission: "platform", canCreate: false, canDelete: false, searchable: false,
    describe: (r) => `${String(r.vehicle_registration ?? r.device)} ${formatDateTime(String(r.start_time))}`,
    filters: [{ param: "vehicle", label: "devices.vehicle",
      choices: (vehicles.data?.results ?? []).map((v) => ({ value: String(v.id), label: v.registration_number })) }],
    columns: [
      { key: "vehicle", header: "devices.vehicle", primary: true, nowrap: true,
        render: (r) => <><div className="font-semibold">{String(r.vehicle_registration ?? "—")}</div><div className="text-xs text-ink-soft">{String(r.driver_name ?? "")}</div></> },
      { key: "start", header: "trips.start", nowrap: true, render: (r) => formatDateTime(String(r.start_time)) },
      { key: "duration", header: "trips.duration", nowrap: true, render: (r) => formatDuration(Number(r.duration_s)) },
      { key: "distance", header: "history.distance", nowrap: true,
        render: (r) => <span title={String(r.distance_source)}>{Number(r.distance_km).toFixed(1)} km{r.distance_source === "odometer" ? " ·" : ""}{r.distance_source === "odometer" ? <Translated k="trips.odometer" /> : null}</span> },
      { key: "speed", header: "history.maxSpeed", nowrap: true, render: (r) => `${Math.round(Number(r.max_speed_kmh))} km/h` },
      { key: "flags", header: "common.status", render: (r) => (r.delayed ? <Badge tone="warn"><Translated k="trips.delayed" /></Badge> : null) },
    ],
    fields: [],
    rowActions: (row) => <ViewRoute row={row} />,
  };
  return <ResourcePage config={config} />;
}
