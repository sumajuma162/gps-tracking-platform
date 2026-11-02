import { useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import axios from "axios";
import { api, errorMessage, fieldErrors, type ApiErrorBody } from "../../services/api";
import { useApiQuery, type Page } from "../../services/hooks";
import { Badge, Button, ErrorState, Field, PageHeader, Select, SkeletonRows, TextInput } from "../../components/ui";
import { Markdown } from "../../components/Markdown";
import { toast } from "../../components/Toast";
import { imeiProblem } from "../../utils/imei";
import { DiagnosticsPanel } from "./DiagnosticsPanel";

interface Model { id: number; manufacturer_name: string; name: string; slug: string; protocol_name: string; protocol_implemented: boolean;
  identifier_type: string; transports: string[]; default_port: number; verified: boolean }
interface Sim { id: number; phone_number: string; operator: string; apn: string; device_name: string | null }
interface Vehicle { id: number; registration_number: string; make: string; model: string; current_device: { name: string } | null }
interface Setup { markdown: string; server_host: string; port: number; transport: string; apn: string }

const STEPS = ["model", "identity", "sim", "vehicle", "review", "connect"] as const;
type Step = (typeof STEPS)[number];

function Stepper({ step }: { step: Step }) {
  const { t } = useTranslation();
  const index = STEPS.indexOf(step);
  return (
    <ol className="mb-6 flex flex-wrap gap-x-5 gap-y-2 text-sm" aria-label={t("wizard.progress")}>
      {STEPS.map((s, i) => (
        <li key={s} aria-current={i === index ? "step" : undefined}
          className={`flex items-center gap-2 ${i === index ? "font-semibold text-ink" : i < index ? "text-moving" : "text-ink-soft"}`}>
          <span className={`flex h-6 w-6 items-center justify-center rounded-full border text-xs ${i === index ? "border-marking bg-marking text-ink" : i < index ? "border-moving" : "border-line"}`}>{i + 1}</span>
          {t(`wizard.step.${s}`)}
        </li>))}
    </ol>
  );
}

function Card({ title, lede, children, actions }: { title: string; lede?: string; children: ReactNode; actions: ReactNode }) {
  return (
    <section className="rounded-lg border border-line bg-panel p-6">
      <h2 className="text-xl font-bold">{title}</h2>
      {lede && <p className="mt-1 text-ink-soft">{lede}</p>}
      <div className="mt-5 space-y-4">{children}</div>
      <div className="mt-6 flex flex-wrap justify-between gap-2 border-t border-line pt-4">{actions}</div>
    </section>
  );
}

export function AddDeviceWizard() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [params] = useSearchParams();
  const [step, setStep] = useState<Step>("model");
  const models = useApiQuery<Page<Model>>("/device-catalog/models/", { active: 1, page_size: 200 });
  const sims = useApiQuery<Page<Sim>>("/sims/", { page_size: 200 });
  const vehicles = useApiQuery<Page<Vehicle>>("/vehicles/", { page_size: 200 });

  const [modelId, setModelId] = useState<number | null>(() => Number(params.get("model")) || null);
  const [identifier, setIdentifier] = useState(params.get("identifier") ?? "");
  const [name, setName] = useState("");
  const [serial, setSerial] = useState("");
  const [firmware, setFirmware] = useState("");
  const [simMode, setSimMode] = useState<"none" | "existing" | "new">("none");
  const [simId, setSimId] = useState("");
  const [newSim, setNewSim] = useState({ phone_number: "", operator: "", apn: "internet" });
  const [vehicleId, setVehicleId] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [deviceId, setDeviceId] = useState<number | null>(null);
  const [createdSimId, setCreatedSimId] = useState<number | null>(null);

  const model = models.data?.results.find((m) => m.id === modelId) ?? null;
  const cleanId = identifier.replace(/\s+/g, "");
  const idProblem = model?.identifier_type === "imei" ? imeiProblem(cleanId) : cleanId ? null : "empty";
  const setup = useApiQuery<Setup>(`/devices/${deviceId}/setup-instructions/`, undefined, { enabled: deviceId !== null });
  const freeSims = useMemo(() => (sims.data?.results ?? []).filter((s) => !s.device_name), [sims.data]);

  const validateIdentity = () => {
    const e: Record<string, string> = {};
    if (idProblem) e.identifier = t(`wizard.imei.${idProblem}`);
    if (!name.trim()) e.name = t("common.required");
    setErrors(e);
    return Object.keys(e).length === 0;
  };
  const validateSim = () => {
    if (simMode === "existing" && !simId) { setErrors({ sim: t("common.required") }); return false; }
    if (simMode === "new" && !/^\+?[0-9 ()-]{6,20}$/.test(newSim.phone_number.trim())) {
      setErrors({ phone_number: t("fields.phoneInvalid") }); return false;
    }
    setErrors({});
    return true;
  };

  const register = async () => {
    setBusy(true); setServerError(null);
    try {
      let sim: number | null = simMode === "existing" ? Number(simId) : createdSimId;
      if (simMode === "new" && sim === null) {
        sim = (await api.post<{ id: number }>("/sims/", newSim)).data.id;
        setCreatedSimId(sim);   // a retry after a later failure must not create the SIM twice
      }
      let id = deviceId;
      if (id === null) {
        id = (await api.post<{ id: number }>("/devices/", { device_model: modelId, identifier: cleanId, name: name.trim(),
          serial_number: serial.trim(), firmware_version: firmware.trim(), sim: simMode === "none" ? null : sim })).data.id;
        setDeviceId(id);
      }
      if (vehicleId) await api.post(`/devices/${id}/assign/`, { vehicle_id: Number(vehicleId) });
      void qc.invalidateQueries();
      toast.success(t("wizard.registered"));
      setStep("connect");
    } catch (err) {
      const f = fieldErrors(err);
      const code = axios.isAxiosError(err) ? (err.response?.data as ApiErrorBody | undefined)?.error?.code : undefined;
      const message = errorMessage(err, t("errors.generic"), t("errors.network"));
      if (f.identifier || code === "identifier_taken") { setErrors({ identifier: f.identifier ?? message }); setStep("identity"); }
      else if (f.phone_number) { setErrors({ phone_number: f.phone_number }); setStep("sim"); }
      setServerError(errorMessage(err, t("errors.generic"), t("errors.network")));
    } finally { setBusy(false); }
  };

  const back = (to: Step) => <Button variant="secondary" onClick={() => { setErrors({}); setStep(to); }}>{t("wizard.back")}</Button>;
  const next = (to: Step, ok: () => boolean = () => true) =>
    <Button onClick={() => { if (ok()) setStep(to); }}>{t("wizard.next")}</Button>;

  if (models.isLoading) return <SkeletonRows />;
  if (models.isError) return <ErrorState message={errorMessage(models.error, t("errors.generic"), t("errors.network"))} onRetry={() => void models.refetch()} />;

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader title={t("wizard.title")} lede={t("wizard.lede")} />
      <Stepper step={step} />
      {serverError && step !== "connect" && <div className="mb-4"><ErrorState message={serverError} /></div>}

      {step === "model" && (
        <Card title={t("wizard.step.model")} lede={t("wizard.modelLede")}
          actions={<><Link to="/app/devices" className="rounded-md px-4 py-2 text-sm font-semibold hover:bg-paper">{t("common.cancel")}</Link>
            {next("identity", () => { if (!modelId) { setErrors({ model: t("wizard.chooseModel") }); return false; } return true; })}</>}>
          <fieldset>
            <legend className="sr-only">{t("wizard.step.model")}</legend>
            <div className="grid gap-3 sm:grid-cols-2">
              {models.data?.results.map((m) => (
                <label key={m.id} className={`flex cursor-pointer gap-3 rounded-md border p-3 ${modelId === m.id ? "border-ink ring-2 ring-marking" : "border-line"} ${m.protocol_implemented ? "" : "cursor-not-allowed opacity-60"}`}>
                  <input type="radio" name="model" value={m.id} checked={modelId === m.id} disabled={!m.protocol_implemented}
                    onChange={() => { setModelId(m.id); setErrors({}); }} className="mt-1" />
                  <span>
                    <span className="block font-semibold">{m.manufacturer_name} {m.name}</span>
                    <span className="block text-sm text-ink-soft">{m.protocol_name} · {m.transports.join("/").toUpperCase()} · {m.default_port}</span>
                    <span className="mt-1 flex flex-wrap gap-1">
                      {!m.protocol_implemented && <Badge>{t("landing.statusInDevelopment")}</Badge>}
                      {!m.verified && m.protocol_implemented && <Badge tone="warn">{t("catalog.verifiedNo")}</Badge>}
                    </span>
                  </span>
                </label>))}
            </div>
          </fieldset>
          {errors.model && <p role="alert" className="text-sm font-medium text-critical">{errors.model}</p>}
        </Card>
      )}

      {step === "identity" && model && (
        <Card title={t("wizard.step.identity")} lede={`${model.manufacturer_name} ${model.name}`}
          actions={<>{back("model")}{next("sim", validateIdentity)}</>}>
          <Field id="w-identifier" label={`${t("fields.identifier")} *`} error={errors.identifier}
            hint={cleanId && !idProblem ? t("wizard.imei.ok") : t("fields.identifierHint")}>
            <TextInput id="w-identifier" inputMode="numeric" autoComplete="off" placeholder="356307042441013" value={identifier}
              invalid={!!errors.identifier} onChange={(e) => { setIdentifier(e.target.value); setErrors({}); }} />
          </Field>
          {cleanId.length === 15 && idProblem === "checksum" && <p className="text-sm text-critical">{t("wizard.imei.checksum")}</p>}
          <Field id="w-name" label={`${t("fields.name")} *`} error={errors.name} hint={t("wizard.nameHint")}>
            <TextInput id="w-name" placeholder="FMC920 T 123 ABC" value={name} invalid={!!errors.name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field id="w-serial" label={t("fields.serial_number")}><TextInput id="w-serial" value={serial} onChange={(e) => setSerial(e.target.value)} /></Field>
            <Field id="w-firmware" label={t("fields.firmware_version")}><TextInput id="w-firmware" placeholder="03.28.07" value={firmware} onChange={(e) => setFirmware(e.target.value)} /></Field>
          </div>
        </Card>
      )}

      {step === "sim" && (
        <Card title={t("wizard.step.sim")} lede={t("wizard.simLede")} actions={<>{back("identity")}{next("vehicle", validateSim)}</>}>
          <fieldset className="space-y-2">
            <legend className="sr-only">{t("wizard.step.sim")}</legend>
            {(["none", "existing", "new"] as const).map((mode) => (
              <label key={mode} className="flex items-center gap-2"><input type="radio" name="simMode" checked={simMode === mode}
                onChange={() => { setSimMode(mode); setErrors({}); }} disabled={mode === "existing" && freeSims.length === 0} />{t(`wizard.sim.${mode}`)}</label>))}
          </fieldset>
          {simMode === "existing" && (
            <Field id="w-sim" label={`${t("fields.sim")} *`} error={errors.sim}>
              <Select id="w-sim" value={simId} onChange={(e) => setSimId(e.target.value)}>
                <option value="">{t("res.choose")}</option>
                {freeSims.map((s) => <option key={s.id} value={s.id}>{s.phone_number}{s.operator ? ` · ${s.operator}` : ""}</option>)}
              </Select>
            </Field>)}
          {simMode === "new" && (
            <div className="grid gap-4 sm:grid-cols-3">
              <Field id="w-phone" label={`${t("fields.phone_number")} *`} error={errors.phone_number}>
                <TextInput id="w-phone" type="tel" placeholder="+255 712 345 678" value={newSim.phone_number} invalid={!!errors.phone_number}
                  onChange={(e) => setNewSim({ ...newSim, phone_number: e.target.value })} /></Field>
              <Field id="w-operator" label={t("fields.operator")}><TextInput id="w-operator" placeholder="Vodacom" value={newSim.operator}
                onChange={(e) => setNewSim({ ...newSim, operator: e.target.value })} /></Field>
              <Field id="w-apn" label="APN" hint={t("wizard.apnHint")}><TextInput id="w-apn" value={newSim.apn}
                onChange={(e) => setNewSim({ ...newSim, apn: e.target.value })} /></Field>
            </div>)}
        </Card>
      )}

      {step === "vehicle" && (
        <Card title={t("wizard.step.vehicle")} lede={t("wizard.vehicleLede")} actions={<>{back("sim")}{next("review")}</>}>
          <Field id="w-vehicle" label={t("devices.vehicle")}>
            <Select id="w-vehicle" value={vehicleId} onChange={(e) => setVehicleId(e.target.value)}>
              <option value="">{t("wizard.notInstalled")}</option>
              {vehicles.data?.results.map((v) => <option key={v.id} value={v.id}>{v.registration_number}{v.current_device ? ` (${t("wizard.replaces", { name: v.current_device.name })})` : ""}</option>)}
            </Select>
          </Field>
        </Card>
      )}

      {step === "review" && model && (
        <Card title={t("wizard.step.review")} lede={t("wizard.reviewLede")}
          actions={<>{back("vehicle")}<Button busy={busy} onClick={() => void register()}>{t("wizard.register")}</Button></>}>
          <dl className="grid gap-3 sm:grid-cols-2">
            {[[t("fields.device_model"), `${model.manufacturer_name} ${model.name}`], [t("fields.identifier"), cleanId], [t("fields.name"), name],
              [t("fields.sim"), simMode === "none" ? t("wizard.sim.none") : simMode === "new" ? newSim.phone_number : freeSims.find((s) => String(s.id) === simId)?.phone_number ?? ""],
              [t("devices.vehicle"), vehicles.data?.results.find((v) => String(v.id) === vehicleId)?.registration_number ?? t("wizard.notInstalled")],
            ].map(([k, v]) => <div key={k}><dt className="text-sm text-ink-soft">{k}</dt><dd className="font-semibold break-all">{v}</dd></div>)}
          </dl>
        </Card>
      )}

      {step === "connect" && deviceId !== null && (
        <section className="grid gap-6 lg:grid-cols-2">
          <div className="rounded-lg border border-line bg-panel p-6">
            <h2 className="text-xl font-bold">{t("wizard.step.connect")}</h2>
            {setup.data ? (
              <>
                <dl className="my-4 grid grid-cols-2 gap-3 rounded-md bg-paper p-3 text-sm">
                  <div><dt className="text-ink-soft">{t("devices.server")}</dt><dd className="break-all font-semibold">{setup.data.server_host}</dd></div>
                  <div><dt className="text-ink-soft">{t("devices.port")}</dt><dd className="font-semibold">{setup.data.port} / {setup.data.transport.toUpperCase()}</dd></div>
                  <div><dt className="text-ink-soft">APN</dt><dd className="font-semibold">{setup.data.apn}</dd></div>
                  <div><dt className="text-ink-soft">IMEI</dt><dd className="font-mono font-semibold">{cleanId}</dd></div>
                </dl>
                <Markdown text={setup.data.markdown} />
              </>) : <SkeletonRows rows={4} />}
          </div>
          <div className="rounded-lg border border-line bg-panel p-6">
            <DiagnosticsPanel deviceId={deviceId} />
            <div className="mt-6 flex justify-end"><Button onClick={() => navigate("/app/devices")}>{t("wizard.finish")}</Button></div>
          </div>
        </section>
      )}
    </div>
  );
}
