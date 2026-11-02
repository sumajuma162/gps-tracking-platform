import { useTranslation } from "react-i18next";
import { useApiQuery, type Page } from "../../services/hooks";
import { ResourcePage } from "../resources/ResourcePage";
import type { ResourceConfig, Row } from "../resources/types";

const TYPES = ["overspeed", "geofence_enter", "geofence_exit", "geofence_dwell", "device_offline", "gps_lost",
  "unauthorized_movement", "ignition_on", "ignition_off", "low_battery", "power_disconnected", "gsm_jamming", "towing",
  "crash", "harsh_driving", "long_idle", "long_stop"];
const THRESHOLD: Record<string, string> = { overspeed: "limit_kmh", long_idle: "minutes", long_stop: "minutes", low_battery: "volts", sim_expiry: "days" };

/** API rule -> flat form values (threshold, geofences, schedule fields). */
export function ruleToForm(r: Row): Row {
  const params = (r.params ?? {}) as Record<string, unknown>;
  const key = THRESHOLD[String(r.type)];
  const schedule = (r.schedule ?? {}) as Record<string, string>;
  return { ...r, threshold: key ? params[key] : "", geofences: params.geofences ?? [], require_driver: Boolean(params.require_driver),
    active_from: schedule.from ?? "", active_to: schedule.to ?? "" };
}

/** Flat form payload -> API rule (params only carry what the alert type uses). */
export function ruleFromForm(p: Record<string, unknown>): Record<string, unknown> {
  const { threshold, geofences, require_driver: requireDriver, active_from: from, active_to: to, ...rest } = p;
  const type = String(rest.type ?? "");
  const params: Record<string, unknown> = {};
  if (THRESHOLD[type] && threshold !== "" && threshold != null) params[THRESHOLD[type]] = Number(threshold);
  if (type.startsWith("geofence_") && Array.isArray(geofences) && geofences.length) params.geofences = geofences.map(Number);
  if (type === "unauthorized_movement" && requireDriver) params.require_driver = true;
  return { ...rest, params, schedule: from && to ? { from, to } : {} };
}

function T({ k }: { k: string }) {
  const { t } = useTranslation();
  return <>{t(k)}</>;
}

export function AlertRulesPage() {
  const vehicles = useApiQuery<Page<{ id: number; registration_number: string }>>("/vehicles/", { page_size: 200 });
  const groups = useApiQuery<Page<{ id: number; name: string }>>("/vehicle-groups/", { page_size: 200 });
  const fences = useApiQuery<Page<{ id: number; name: string }>>("/geofences/", { page_size: 200 });
  const roles = useApiQuery<Page<{ slug: string; name: string; is_system: boolean }>>("/roles/", { page_size: 100 });
  const config: ResourceConfig = {
    key: "alertRules", path: "/alert-rules/", title: "nav.alertRules", lede: "rules.lede", empty: "rules.empty",
    viewPermission: "alerts.view", managePermission: "alerts.manage", canDelete: true,
    describe: (r) => String(r.name),
    columns: [
      { key: "name", header: "fields.name", primary: true, render: (r) => <span className="font-semibold">{String(r.name)}</span> },
      { key: "type", header: "rules.type", render: (r) => <T k={`alerts.type.${String(r.type)}`} /> },
      { key: "severity", header: "alerts.severity", render: (r) => <T k={`alerts.sev.${String(r.severity)}`} /> },
      { key: "cooldown_minutes", header: "rules.cooldown", render: (r) => `${String(r.cooldown_minutes)} min` },
      { key: "is_active", header: "common.status", render: (r) => <T k={r.is_active ? "common.active" : "common.inactive"} /> },
    ],
    toForm: ruleToForm,
    fromForm: ruleFromForm,
    fields: [
      { name: "name", label: "fields.name", required: true },
      { name: "type", label: "rules.type", type: "select", required: true, choices: TYPES.map((v) => ({ value: v, label: `alerts.type.${v}` })) },
      { name: "severity", label: "alerts.severity", type: "select", required: true,
        choices: ["info", "warning", "critical"].map((v) => ({ value: v, label: `alerts.sev.${v}` })) },
      { name: "threshold", label: "rules.threshold", type: "number", hint: "rules.thresholdHint" },
      { name: "geofences", label: "nav.geofences", type: "multiselect", hint: "rules.geofencesHint",
        choices: (fences.data?.results ?? []).map((g) => ({ value: String(g.id), label: g.name })) },
      { name: "require_driver", label: "rules.requireDriver", type: "checkbox" },
      { name: "active_from", label: "rules.activeFrom", type: "time", hint: "rules.activeHint" },
      { name: "active_to", label: "rules.activeTo", type: "time" },
      { name: "applies_to_all", label: "rules.allVehicles", type: "checkbox", defaultValue: true },
      { name: "vehicles", label: "nav.vehicles", type: "multiselect", choices: (vehicles.data?.results ?? []).map((v) => ({ value: String(v.id), label: v.registration_number })) },
      { name: "groups", label: "vehicles.groups", type: "multiselect", choices: (groups.data?.results ?? []).map((g) => ({ value: String(g.id), label: g.name })) },
      { name: "recipient_roles", label: "rules.recipients", type: "multiselect", required: true, choices: (roles.data?.results ?? []).filter((r) => r.is_system && r.slug !== "driver" && r.slug !== "auditor")
          .map((r) => ({ value: r.slug, label: r.name })) },
      { name: "cooldown_minutes", label: "rules.cooldown", type: "number", defaultValue: 10 },
      { name: "is_active", label: "common.active", type: "checkbox", defaultValue: true },
    ],
  };
  return <ResourcePage config={config} />;
}
