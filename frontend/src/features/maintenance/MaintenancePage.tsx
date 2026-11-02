import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge, Button, formatDateTime } from "../../components/ui";
import { activeMembership, useAuth } from "../../store/auth";
import { ResourcePage } from "../resources/ResourcePage";
import type { ResourceConfig, Row } from "../resources/types";

interface Due { state: "ok" | "due_soon" | "overdue"; km_left: number | null; hours_left: number | null; days_left: number | null }
const TONE = { ok: "good", due_soon: "warn", overdue: "bad" } as const;

function DueCell({ row }: { row: Row }) {
  const { t } = useTranslation();
  const s = row.status as Due;
  const parts = ([["km_left", "km"], ["hours_left", "h"], ["days_left", "d"]] as const)
    .filter(([k]) => s[k] !== null).map(([k, unit]) => {
      const v = s[k] as number;
      return t(v >= 0 ? "maintenance.left" : "maintenance.over", { value: `${Math.abs(v)} ${unit}` });
    });
  return <><Badge tone={TONE[s.state]}>{t(`maintenance.state.${s.state}`)}</Badge><div className="text-xs text-ink-soft">{parts.join(" · ")}</div></>;
}

export function MaintenancePage() {
  const { t } = useTranslation();
  const tz = activeMembership(useAuth())?.organization.timezone;
  const [tab, setTab] = useState<"schedules" | "records">("schedules");
  const vehicle = { name: "vehicle", label: "nav.vehicles", type: "select" as const, required: true, createOnly: true,
    options: { path: "/vehicles/", label: (r: Row) => String(r.registration_number) } };
  const schedules: ResourceConfig = {
    key: "maintenance-schedules", path: "/maintenance/schedules/", title: "nav.maintenance", lede: "maintenance.lede",
    viewPermission: "maintenance.view", managePermission: "maintenance.manage", empty: "maintenance.emptySchedules",
    describe: (r) => `${String(r.vehicle_registration)} ${String(r.name)}`,
    columns: [
      { key: "vehicle", header: "nav.vehicles", primary: true, nowrap: true, render: (r) => <><div className="font-semibold">{String(r.vehicle_registration)}</div><div>{String(r.name)}</div></> },
      { key: "every", header: "maintenance.interval", render: (r) => [r.interval_km && `${String(r.interval_km)} km`, r.interval_engine_hours && `${String(r.interval_engine_hours)} h`, r.interval_days && `${String(r.interval_days)} d`].filter(Boolean).join(" / ") },
      { key: "last", header: "maintenance.lastService", render: (r) => formatDateTime(r.last_service_at as string, tz) },
      { key: "due", header: "maintenance.nextDue", render: (r) => <DueCell row={r} /> },
    ],
    fields: [
      vehicle,
      { name: "name", label: "maintenance.task", required: true, maxLength: 100, placeholder: "Oil change" },
      { name: "interval_km", label: "maintenance.intervalKm", type: "number", min: 1, allowNull: true },
      { name: "interval_engine_hours", label: "maintenance.intervalHours", type: "number", min: 1, allowNull: true },
      { name: "interval_days", label: "maintenance.intervalDays", type: "number", min: 1, allowNull: true },
      { name: "due_soon_km", label: "maintenance.dueSoonKm", type: "number", min: 0, defaultValue: 500 },
      { name: "due_soon_engine_hours", label: "maintenance.dueSoonHours", type: "number", min: 0, defaultValue: 10 },
      { name: "due_soon_days", label: "maintenance.dueSoonDays", type: "number", min: 0, defaultValue: 7 },
      { name: "is_active", label: "common.active", type: "checkbox", defaultValue: true },
      { name: "notes", label: "fields.notes", type: "textarea" },
    ],
  };
  const records: ResourceConfig = {
    key: "maintenance-records", path: "/maintenance/records/", title: "nav.maintenance", lede: "maintenance.lede",
    viewPermission: "maintenance.view", managePermission: "maintenance.manage", empty: "maintenance.emptyRecords",
    describe: (r) => String(r.title),
    columns: [
      { key: "when", header: "maintenance.performedAt", primary: true, nowrap: true, render: (r) => <><div className="font-semibold">{String(r.vehicle_registration)}</div><div>{formatDateTime(r.performed_at as string, tz)}</div></> },
      { key: "title", header: "maintenance.title", render: (r) => <>{String(r.title)}{r.schedule_name ? <div className="text-xs text-ink-soft">{String(r.schedule_name)}</div> : null}</> },
      { key: "cost", header: "maintenance.cost", render: (r) => r.cost ? `${Number(r.cost).toLocaleString()} ${String(r.currency)}` : "—" },
      { key: "vendor", header: "maintenance.vendor", render: (r) => String(r.vendor || "—") },
    ],
    fields: [
      vehicle,
      { name: "schedule", label: "maintenance.schedule", type: "select", allowNull: true, options: { path: "/maintenance/schedules/", label: (r: Row) => `${String(r.vehicle_registration)} — ${String(r.name)}` } },
      { name: "title", label: "maintenance.title", required: true, maxLength: 150 },
      { name: "performed_at", label: "maintenance.performedAt", type: "date", required: true },
      { name: "cost", label: "maintenance.cost", type: "number", step: "0.01", min: 0, allowNull: true },
      { name: "currency", label: "maintenance.currency", maxLength: 3, defaultValue: "TZS" },
      { name: "vendor", label: "maintenance.vendor", maxLength: 120 },
      { name: "notes", label: "fields.notes", type: "textarea" },
    ],
    toForm: (r) => ({ ...r, performed_at: String(r.performed_at ?? "").slice(0, 10) }),
    fromForm: (p) => ({ ...p, performed_at: typeof p.performed_at === "string" && p.performed_at.length === 10 ? `${p.performed_at}T12:00:00` : p.performed_at }),
  };
  return (
    <>
      <div role="tablist" className="mb-2 flex gap-2">
        <Button role="tab" aria-selected={tab === "schedules"} variant={tab === "schedules" ? "primary" : "secondary"} onClick={() => setTab("schedules")}>{t("maintenance.schedules")}</Button>
        <Button role="tab" aria-selected={tab === "records"} variant={tab === "records" ? "primary" : "secondary"} onClick={() => setTab("records")}>{t("maintenance.records")}</Button>
      </div>
      <ResourcePage key={tab} config={tab === "schedules" ? schedules : records} />
    </>
  );
}
