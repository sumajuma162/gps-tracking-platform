import { useState } from "react";
import { CommandsDialog } from "./CommandsDialog";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api, errorMessage } from "../../services/api";
import { Badge, Button, formatDateTime } from "../../components/ui";
import { toast } from "../../components/Toast";
import { ResourcePage } from "../resources/ResourcePage";
import { StatusBadge } from "../resources/StatusBadges";
import type { ResourceConfig, Row } from "../resources/types";
import { AssignDialog, CloneModelDialog, HistoryDialog, ImportDialog, SetupInstructionsDialog } from "./dialogs";
import { Dialog } from "../../components/Dialog";
import { DiagnosticsPanel } from "../devices/DiagnosticsPanel";
import { OneTimeSecret } from "../integrations/IntegrationsPage";

const choices = (values: string[]) => values.map((v) => ({ value: v, label: `choice.${v}` }));
const text = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : String(v));
type Ref = { id: number; name?: string; registration_number?: string; identifier?: string } | null;
const ref = (row: Row, key: string) => row[key] as Ref;
const PHONE = { value: /^\+?[0-9 ()-]{6,20}$/, message: "fields.phoneInvalid" };

/** Translate the message of a pattern rule at render time. */
function usePhone() {
  const { t } = useTranslation();
  return { value: PHONE.value, message: t(PHONE.message) };
}

// ---- Vehicles -------------------------------------------------------------------------------------

function VehicleActions({ row, refresh, canManage }: { row: Row; refresh: () => void; canManage: boolean }) {
  const { t } = useTranslation();
  const [dialog, setDialog] = useState<"driver" | "history" | null>(null);
  const driver = ref(row, "current_driver");
  const unassignDriver = async () => {
    try { await api.post(`/vehicles/${row.id}/unassign-driver/`); toast.success(t("res.saved")); refresh(); }
    catch (err) { toast.error(errorMessage(err, t("errors.generic"), t("errors.network"))); }
  };
  return (
    <>
      <Button variant="ghost" onClick={() => setDialog("history")}>{t("res.history")}</Button>
      {canManage && (driver
        ? <Button variant="ghost" onClick={() => void unassignDriver()}>{t("vehicles.removeDriver")}</Button>
        : <Button variant="ghost" onClick={() => setDialog("driver")}>{t("vehicles.assignDriver")}</Button>)}
      {dialog === "history" && <HistoryDialog vehicle={row} onClose={() => setDialog(null)} />}
      {dialog === "driver" && (
        <AssignDialog title={t("vehicles.assignDriver")} url={`/vehicles/${row.id}/assign-driver/`} field="driver_id"
          source="/drivers/" label={(d) => String(d.full_name)} onClose={() => setDialog(null)}
          onDone={() => { setDialog(null); refresh(); }} />)}
    </>
  );
}

export function VehiclesPage() {
  const config: ResourceConfig = {
    key: "vehicles", path: "/vehicles/", title: "nav.vehicles", lede: "vehicles.lede",
    viewPermission: "vehicles.view", managePermission: "vehicles.manage", empty: "vehicles.empty",
    describe: (r) => String(r.registration_number),
    columns: [
      { key: "reg", header: "fields.registration_number", primary: true, nowrap: true,
        render: (r) => <><div className="font-semibold">{String(r.registration_number)}</div><div className="text-ink-soft">{[r.make, r.model].filter(Boolean).join(" ")}</div></> },
      { key: "type", header: "fields.vehicle_type", render: (r) => <ChoiceText value={r.vehicle_type} /> },
      { key: "device", header: "vehicles.tracker", render: (r) => text(ref(r, "current_device")?.name) },
      { key: "driver", header: "vehicles.driver", render: (r) => text(ref(r, "current_driver")?.name) },
      { key: "status", header: "common.status", render: (r) => <StatusBadge value={r.status} /> },
    ],
    filters: [{ param: "status", label: "common.status", choices: choices(["active", "maintenance", "inactive"]) }],
    fields: [
      { name: "registration_number", label: "fields.registration_number", required: true, maxLength: 20, placeholder: "T 123 ABC" },
      { name: "vehicle_type", label: "fields.vehicle_type", choices: choices(["truck", "pickup", "van", "car", "bus", "motorcycle", "trailer", "machinery", "other"]), required: true },
      { name: "make", label: "fields.make", maxLength: 60, placeholder: "Isuzu" },
      { name: "model", label: "fields.model", maxLength: 60, placeholder: "FVR" },
      { name: "year", label: "fields.year", type: "number", min: 1950, max: 2100, placeholder: "2021", allowNull: true },
      { name: "color", label: "fields.color", maxLength: 30, placeholder: "White" },
      { name: "fuel_type", label: "fields.fuel_type", choices: choices(["diesel", "petrol", "electric", "hybrid", "lpg", "other"]), required: true },
      { name: "fuel_capacity_l", label: "fields.fuel_capacity_l", type: "number", min: 0, step: "0.1", placeholder: "200", allowNull: true },
      { name: "odometer_km", label: "fields.odometer_km", type: "number", min: 0, step: "0.1", placeholder: "0" },
      { name: "status", label: "common.status", choices: choices(["active", "maintenance", "inactive"]), required: true },
      { name: "vin", label: "fields.vin", maxLength: 17, placeholder: "JALFRR90PJ7000001", hint: "fields.vinHint" },
      { name: "groups", label: "fields.groups", type: "multiselect", options: { path: "/vehicle-groups/", label: (g) => String(g.name) } },
      { name: "notes", label: "fields.notes", type: "textarea" },
    ],
    rowActions: (row, h) => <VehicleActions row={row} refresh={h.refresh} canManage={h.canManage} />,
    headerActions: () => <Link to="/app/vehicle-groups" className="rounded-md border border-line bg-panel px-4 py-2 text-sm font-semibold hover:border-ink-soft"><GroupsLabel /></Link>,
  };
  return <ResourcePage config={config} />;
}

function GroupsLabel() {
  const { t } = useTranslation();
  return <>{t("vehicles.groups")}</>;
}

function ChoiceText({ value }: { value: unknown }) {
  const { t } = useTranslation();
  return <>{value ? t(`choice.${String(value)}`, { defaultValue: String(value) }) : "—"}</>;
}

export function VehicleGroupsPage() {
  const config: ResourceConfig = {
    key: "groups", path: "/vehicle-groups/", title: "vehicles.groups", lede: "vehicles.groupsLede",
    viewPermission: "vehicles.view", managePermission: "vehicles.manage", empty: "vehicles.groupsEmpty",
    describe: (r) => String(r.name),
    columns: [
      { key: "name", header: "fields.name", primary: true, render: (r) => <span className="font-semibold">{String(r.name)}</span> },
      { key: "description", header: "fields.description", render: (r) => text(r.description) },
      { key: "count", header: "nav.vehicles", render: (r) => String(r.vehicle_count) },
    ],
    fields: [
      { name: "name", label: "fields.name", required: true, maxLength: 100, placeholder: "Upcountry routes" },
      { name: "description", label: "fields.description", maxLength: 300 },
    ],
  };
  return <ResourcePage config={config} />;
}

// ---- Trackers -------------------------------------------------------------------------------------

function DeviceActions({ row, refresh, canManage }: { row: Row; refresh: () => void; canManage: boolean }) {
  const { t } = useTranslation();
  const [dialog, setDialog] = useState<"assign" | "setup" | "test" | "commands" | "token" | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const newToken = async () => {
    try { setToken((await api.post<{ token: string }>(`/devices/${row.id}/rotate-token/`)).data.token); }
    catch (err) { toast.error(errorMessage(err, t("errors.generic"), t("errors.network"))); }
  };
  const vehicle = ref(row, "current_vehicle");
  const post = async (action: string, done: string) => {
    try { await api.post(`/devices/${row.id}/${action}/`); toast.success(t(done)); refresh(); }
    catch (err) { toast.error(errorMessage(err, t("errors.generic"), t("errors.network"))); }
  };
  return (
    <>
      <Button variant="ghost" onClick={() => setDialog("test")}>{t("devices.connectionTest")}</Button>
      <Button variant="ghost" onClick={() => setDialog("setup")}>{t("devices.setup")}</Button>
      <Button variant="ghost" onClick={() => setDialog("commands")}>{t("commands.button")}</Button>
      {canManage && row.protocol === "generic_json" && <Button variant="ghost" onClick={() => { setToken(null); setDialog("token"); }}>{t("formats.token")}</Button>}
      {dialog === "token" && (
        <Dialog open title={t("formats.token")} onClose={() => setDialog(null)}>
          {token ? <OneTimeSecret value={token} message={t("formats.tokenOnce")} />
            : <Button onClick={() => void newToken()}>{t("formats.tokenNew")}</Button>}
        </Dialog>)}
      {canManage && (vehicle
        ? <Button variant="ghost" onClick={() => void post("unassign", "res.saved")}>{t("devices.unassign")}</Button>
        : row.is_active ? <Button variant="ghost" onClick={() => setDialog("assign")}>{t("devices.assign")}</Button> : null)}
      {canManage && (row.is_active
        ? <Button variant="ghost" onClick={() => void post("disable", "devices.disabled")}>{t("devices.disable")}</Button>
        : <Button variant="ghost" onClick={() => void post("enable", "devices.enabled")}>{t("devices.enable")}</Button>)}
      {dialog === "setup" && <SetupInstructionsDialog device={row} onClose={() => setDialog(null)} />}
      {dialog === "commands" && <CommandsDialog device={row} onClose={() => setDialog(null)} />}
      {dialog === "test" && (
        <Dialog open wide title={t("devices.connectionTestTitle", { name: String(row.name) })} onClose={() => { setDialog(null); refresh(); }}>
          <DiagnosticsPanel deviceId={row.id} />
        </Dialog>)}
      {dialog === "assign" && (
        <AssignDialog title={t("devices.assign")} url={`/devices/${row.id}/assign/`} field="vehicle_id" source="/vehicles/"
          label={(v) => String(v.registration_number)} onClose={() => setDialog(null)} onDone={() => { setDialog(null); refresh(); }} />)}
    </>
  );
}

function ImportButton({ refresh }: { refresh: () => void }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="secondary" onClick={() => setOpen(true)}>{t("devices.import")}</Button>
      {open && <ImportDialog onClose={() => setOpen(false)} onDone={() => { setOpen(false); refresh(); }} />}
    </>
  );
}

export function DevicesPage() {
  const config: ResourceConfig = {
    key: "devices", path: "/devices/", title: "nav.devices", lede: "devices.lede",
    viewPermission: "devices.view", managePermission: "devices.manage", empty: "devices.empty",
    describe: (r) => `${String(r.name)} (${String(r.identifier)})`,
    columns: [
      { key: "name", header: "fields.name", primary: true,
        render: (r) => <><div className="font-semibold">{String(r.name)}</div><div className="font-mono text-xs text-ink-soft">{String(r.identifier)}</div></> },
      { key: "model", header: "fields.device_model", render: (r) => text(r.model_name) },
      { key: "vehicle", header: "devices.vehicle", nowrap: true, render: (r) => text(ref(r, "current_vehicle")?.registration_number) },
      { key: "state", header: "common.status", render: (r) => <span className="flex flex-wrap gap-1">
        {r.is_active ? <Badge tone="good">{t_("devices.enabledBadge")}</Badge> : <Badge tone="bad">{t_("devices.disabledBadge")}</Badge>}
        <StatusBadge value={r.inventory_status} /></span> },
      { key: "seen", header: "devices.lastPacket", render: (r) => <LastSeen value={r.last_packet_at} /> },
    ],
    filters: [
      { param: "active", label: "devices.enabledFilter", choices: [{ value: "1", label: "devices.enabledBadge" }, { value: "0", label: "devices.disabledBadge" }] },
      { param: "assigned", label: "devices.assignedFilter", choices: [{ value: "1", label: "devices.inVehicle" }, { value: "0", label: "devices.notInVehicle" }] },
    ],
    fields: [
      { name: "device_model", label: "fields.device_model", type: "select", required: true,
        options: { path: "/device-catalog/models/", params: { active: 1 }, label: (m) => `${String(m.manufacturer_name)} ${String(m.name)}${m.protocol_implemented ? "" : " (in development)"}` } },
      { name: "identifier", label: "fields.identifier", required: true, maxLength: 40, createOnly: true, placeholder: "356307042441013", hint: "fields.identifierHint" },
      { name: "name", label: "fields.name", required: true, maxLength: 100, placeholder: "Truck T 123 ABC" },
      { name: "serial_number", label: "fields.serial_number", maxLength: 60 },
      { name: "sim", label: "fields.sim", type: "select", options: { path: "/sims/", label: (s) => `${String(s.phone_number)}${s.operator ? ` · ${String(s.operator)}` : ""}` }, allowNull: true },
      { name: "firmware_version", label: "fields.firmware_version", maxLength: 60, placeholder: "03.28.07" },
      { name: "inventory_status", label: "fields.inventory_status", choices: choices(["in_stock", "installed", "faulty", "returned", "retired"]), required: true },
      { name: "installation_date", label: "fields.installation_date", type: "date", allowNull: true },
      { name: "log_raw_packets", label: "fields.log_raw_packets", type: "checkbox" },
      { name: "notes", label: "fields.notes", type: "textarea" },
    ],
    canCreate: false,  // trackers are added with the Add Device wizard (spec A7)
    rowActions: (row, h) => <DeviceActions row={row} refresh={h.refresh} canManage={h.canManage} />,
    headerActions: (h) => (h.canManage ? <><ImportButton refresh={h.refresh} />
      <Link to="/app/devices/new" className="rounded-md bg-ink px-4 py-2 text-sm font-semibold text-white hover:bg-ink-deep"><Translated k="devices.addTracker" /></Link></> : null),
  };
  return <ResourcePage config={config} />;
}

/** Badge text rendered through i18n inside column renderers. */
function t_(key: string) { return <Translated k={key} />; }
function Translated({ k }: { k: string }) { const { t } = useTranslation(); return <>{t(k)}</>; }
function LastSeen({ value }: { value: unknown }) {
  const { t } = useTranslation();
  return <>{value ? formatDateTime(String(value)) : t("devices.neverConnected")}</>;
}

// ---- Drivers --------------------------------------------------------------------------------------

export function DriversPage() {
  const phone = usePhone();
  const config: ResourceConfig = {
    key: "drivers", path: "/drivers/", title: "nav.drivers", lede: "drivers.lede",
    viewPermission: "drivers.view", managePermission: "drivers.manage", empty: "drivers.empty",
    describe: (r) => String(r.full_name),
    columns: [
      { key: "name", header: "fields.name", primary: true,
        render: (r) => <><div className="font-semibold">{String(r.full_name)}</div><div className="text-ink-soft">{text(r.employee_number)}</div></> },
      { key: "phone", header: "fields.phone", render: (r) => text(r.phone) },
      { key: "license", header: "drivers.license", render: (r) => <span className="flex flex-wrap items-center gap-1">{text(r.license_number)}
        {Boolean(r.license_expired) && <Badge tone="bad">{t_("drivers.expired")}</Badge>}</span> },
      { key: "vehicle", header: "drivers.vehicle", nowrap: true, render: (r) => text(ref(r, "current_vehicle")?.registration_number) },
      { key: "status", header: "common.status", render: (r) => <StatusBadge value={r.status} /> },
    ],
    filters: [{ param: "status", label: "common.status", choices: choices(["active", "inactive"]) }],
    fields: [
      { name: "first_name", label: "fields.first_name", required: true, maxLength: 60, placeholder: "Hamisi" },
      { name: "middle_name", label: "fields.middle_name", maxLength: 60 },
      { name: "last_name", label: "fields.last_name", required: true, maxLength: 60, placeholder: "Juma" },
      { name: "employee_number", label: "fields.employee_number", maxLength: 30, placeholder: "DL-0042" },
      { name: "phone", label: "fields.phone", type: "tel", pattern: phone, placeholder: "+255 712 345 678" },
      { name: "email", label: "common.email", type: "email" },
      { name: "license_number", label: "fields.license_number", maxLength: 30 },
      { name: "license_class", label: "fields.license_class", maxLength: 20, placeholder: "C, E" },
      { name: "license_expiry", label: "fields.license_expiry", type: "date", allowNull: true },
      { name: "status", label: "common.status", choices: choices(["active", "inactive"]), required: true },
      { name: "emergency_contact_name", label: "fields.emergency_contact_name", maxLength: 100 },
      { name: "emergency_contact_phone", label: "fields.emergency_contact_phone", type: "tel", pattern: phone },
      { name: "user", label: "driver.portalLogin", type: "select", allowNull: true,
        options: { path: "/users/", label: (m) => `${String(m.full_name || m.email)} (${String(m.email)})`, value: (m) => Number(m.user) } },
    ],
  };
  return <ResourcePage config={config} />;
}

// ---- SIM cards ------------------------------------------------------------------------------------

export function SimsPage() {
  const phone = usePhone();
  const config: ResourceConfig = {
    key: "sims", path: "/sims/", title: "nav.sims", lede: "sims.lede",
    viewPermission: "sims.view", managePermission: "sims.manage", empty: "sims.empty",
    describe: (r) => String(r.phone_number),
    columns: [
      { key: "phone", header: "fields.phone_number", primary: true, nowrap: true,
        render: (r) => <><div className="font-semibold">{String(r.phone_number)}</div><div className="font-mono text-xs text-ink-soft">{text(r.iccid)}</div></> },
      { key: "operator", header: "fields.operator", render: (r) => text(r.operator) },
      { key: "apn", header: "APN", render: (r) => text(r.apn) },
      { key: "device", header: "sims.tracker", render: (r) => text(r.device_name) },
      { key: "expiry", header: "fields.expiry_date", render: (r) => text(r.expiry_date) },
      { key: "status", header: "common.status", render: (r) => <StatusBadge value={r.status} /> },
    ],
    fields: [
      { name: "phone_number", label: "fields.phone_number", required: true, type: "tel", pattern: phone, placeholder: "+255 712 345 678" },
      { name: "iccid", label: "fields.iccid", maxLength: 22, pattern: { value: /^[0-9]{18,22}$/, message: "18-22 digits" } },
      { name: "operator", label: "fields.operator", maxLength: 60, placeholder: "Vodacom" },
      { name: "apn", label: "APN", maxLength: 100, placeholder: "internet" },
      { name: "apn_username", label: "fields.apn_username", maxLength: 60 },
      { name: "apn_password", label: "fields.apn_password", type: "password", maxLength: 60, writeOnlyBlankOnEdit: true, hint: "sims.passwordHint" },
      { name: "data_plan", label: "fields.data_plan", maxLength: 100, placeholder: "1 GB / month" },
      { name: "expiry_date", label: "fields.expiry_date", type: "date", allowNull: true },
      { name: "status", label: "common.status", choices: choices(["active", "suspended", "cancelled"]), required: true },
      { name: "notes", label: "fields.notes", type: "textarea" },
    ],
  };
  return <ResourcePage config={config} />;
}

// ---- Platform: device catalog and listener ports -------------------------------------------------

function ModelActions({ row, refresh }: { row: Row; refresh: () => void }) {
  const { t } = useTranslation();
  const [cloning, setCloning] = useState(false);
  return (
    <>
      {row.io_profile ? <Link to={`/app/device-catalog/io-profiles/${String(row.io_profile)}`}
        className="rounded-md px-2.5 py-1.5 text-sm font-semibold hover:bg-paper">{t("catalog.ioMapping")}</Link> : null}
      <Button variant="ghost" onClick={() => setCloning(true)}>{t("catalog.clone")}</Button>
      {cloning && <CloneModelDialog model={row} onClose={() => setCloning(false)} onDone={() => { setCloning(false); refresh(); }} />}
    </>
  );
}

export function DeviceCatalogPage() {
  const config: ResourceConfig = {
    key: "catalog", path: "/device-catalog/models/", title: "nav.catalog", lede: "catalog.lede",
    viewPermission: "platform", managePermission: "platform", empty: "catalog.empty", canCreate: false, canDelete: false,
    describe: (r) => `${String(r.manufacturer_name)} ${String(r.name)}`,
    columns: [
      { key: "name", header: "fields.device_model", primary: true,
        render: (r) => <><div className="font-semibold">{String(r.manufacturer_name)} {String(r.name)}</div><div className="font-mono text-xs text-ink-soft">{String(r.slug)}</div></> },
      { key: "protocol", header: "catalog.protocol", render: (r) => <span className="flex flex-wrap items-center gap-1">{String(r.protocol_name)}
        {r.protocol_implemented ? null : <Badge>{t_("landing.statusInDevelopment")}</Badge>}</span> },
      { key: "transport", header: "devices.transport", render: (r) => `${(r.transports as string[]).join(", ").toUpperCase()} · ${String(r.default_port)}` },
      { key: "verified", header: "catalog.verified", render: (r) => (r.verified ? <Badge tone="good">{t_("catalog.verifiedYes")}</Badge> : <Badge tone="warn">{t_("catalog.verifiedNo")}</Badge>) },
      { key: "status", header: "common.status", render: (r) => <StatusBadge value={r.status} /> },
    ],
    fields: [
      { name: "name", label: "fields.name", required: true, maxLength: 100 },
      { name: "status", label: "common.status", choices: choices(["active", "deprecated"]), required: true },
      { name: "default_port", label: "devices.port", type: "number", min: 1, max: 65535, required: true },
      { name: "trip_detection_mode", label: "catalog.tripMode", choices: choices(["ignition", "motion", "speed"]), required: true },
      { name: "generic_format", label: "formats.model", type: "select", allowNull: true, options: { path: "/device-catalog/generic-formats/", label: (f) => `${String(f.name)} (${String(f.slug)})` } },
      { name: "time_offset_minutes", label: "catalog.timeOffset", type: "number", min: -840, max: 840, hint: "catalog.timeOffsetHint" },
      { name: "verified", label: "catalog.verifiedField", type: "checkbox" },
      { name: "is_public", label: "catalog.isPublic", type: "checkbox" },
      { name: "setup_instructions", label: "catalog.instructions", type: "textarea", hint: "catalog.instructionsHint" },
    ],
    rowActions: (row, h) => <ModelActions row={row} refresh={h.refresh} />,
  };
  return <ResourcePage config={config} />;
}

export function ListenerPortsPage() {
  const config: ResourceConfig = {
    key: "ports", path: "/device-catalog/listener-ports/", title: "nav.ports", lede: "ports.lede",
    viewPermission: "platform", managePermission: "platform", empty: "ports.empty",
    describe: (r) => `${String(r.port)}/${String(r.transport).toUpperCase()}`,
    columns: [
      { key: "port", header: "devices.port", primary: true, render: (r) => <span className="font-semibold">{String(r.port)}/{String(r.transport).toUpperCase()}</span> },
      { key: "desc", header: "fields.description", render: (r) => <>{text(r.description)}{r.warning ? <p className="mt-1 text-critical">{String(r.warning)}</p> : null}</> },
      { key: "enabled", header: "common.status", render: (r) => (r.is_enabled ? <Badge tone="good">{t_("devices.enabledBadge")}</Badge> : <Badge>{t_("devices.disabledBadge")}</Badge>) },
    ],
    fields: [
      { name: "port", label: "devices.port", type: "number", min: 1024, max: 65535, required: true, placeholder: "5100" },
      { name: "transport", label: "devices.transport", choices: [{ value: "tcp", label: "TCP" }, { value: "udp", label: "UDP" }, { value: "http", label: "HTTP" }], required: true },
      { name: "protocol", label: "catalog.protocol", type: "select", required: true, options: { path: "/device-catalog/protocols/", label: (p) => String(p.display_name) } },
      { name: "generic_format", label: "formats.port", type: "select", allowNull: true, options: { path: "/device-catalog/generic-formats/", label: (f) => `${String(f.name)} (${String(f.slug)})` } },
      { name: "is_enabled", label: "ports.enabled", type: "checkbox" },
      { name: "description", label: "fields.description", maxLength: 200 },
    ],
  };
  return <ResourcePage config={config} />;
}
