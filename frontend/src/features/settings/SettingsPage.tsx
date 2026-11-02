import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { api, errorMessage } from "../../services/api";
import { useApiQuery } from "../../services/hooks";
import { hasPermission, useAuth } from "../../store/auth";
import { Button, ErrorState, Field, PageHeader, Select, SkeletonRows, TextInput } from "../../components/ui";
import { toast } from "../../components/Toast";

export interface SettingDef { key: string; group: string; min: number | null; max: number | null; choices: string[]; integer: boolean }
type Values = Record<string, number | string>;
interface OrgBody { organization: { name: string; timezone: string; language: string; distance_unit: string }; values: Values; overrides: Values; defaults: Values; schema: SettingDef[] }
interface PlatformBody { values: Values; overrides: Values; builtin: Values; schema: SettingDef[]; map_tile_url: string; map_attribution: string; geocoder_provider: string; geocoder_url: string; env_map_tile_url: string }

const GROUPS = ["status", "trips", "speed", "geofences", "map", "data", "notifications"];
const TIMEZONES = ["Africa/Dar_es_Salaam", "Africa/Nairobi", "Africa/Kampala", "Africa/Kigali", "Africa/Lusaka", "Africa/Johannesburg", "Africa/Lagos", "UTC"];

/** One input per setting, grouped; shows the inherited default and whether this level overrides it. */
export function SettingsFields({ schema, draft, inherited, overrides, onChange, idPrefix }: {
  schema: SettingDef[]; draft: Values; inherited: Values; overrides: Values; onChange: (key: string, value: string | null) => void; idPrefix: string;
}) {
  const { t } = useTranslation();
  return (
    <div className="space-y-6">
      {GROUPS.map((group) => {
        const items = schema.filter((s) => s.group === group);
        if (!items.length) return null;
        return (
          <fieldset key={group} className="rounded-md border border-line bg-panel p-4">
            <legend className="px-1 font-bold">{t(`settings.group.${group}`)}</legend>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {items.map((s) => {
                const id = `${idPrefix}-${s.key}`;
                const own = s.key in overrides || s.key in draft;
                const value = s.key in draft ? draft[s.key] : (overrides[s.key] ?? "");
                return (
                  <Field key={s.key} id={id} label={t(`settings.key.${s.key}`)}
                    hint={t("settings.inherited", { value: String(inherited[s.key]) })}>
                    <div className="flex gap-2">
                      {s.choices.length ? (
                        <Select id={id} value={String(value)} onChange={(e) => onChange(s.key, e.target.value || null)}>
                          <option value="">{t("settings.reset")}</option>
                          {s.choices.map((c) => <option key={c} value={c}>{t(`alerts.sev.${c}`, { defaultValue: c })}</option>)}
                        </Select>
                      ) : (
                        <TextInput id={id} type="number" inputMode="decimal" step={s.integer ? 1 : "any"}
                          min={s.min ?? undefined} max={s.max ?? undefined} placeholder={String(inherited[s.key])}
                          value={String(value)} onChange={(e) => onChange(s.key, e.target.value === "" ? null : e.target.value)} />
                      )}
                      {own && value !== "" && <Button type="button" variant="ghost" onClick={() => onChange(s.key, null)}>{t("settings.reset")}</Button>}
                    </div>
                  </Field>);
              })}
            </div>
          </fieldset>);
      })}
    </div>
  );
}

/** Draft edits -> API payload: numbers as numbers, cleared fields as null (back to the default). */
export function toPayload(schema: SettingDef[], draft: Values, nulls: Set<string>): Record<string, number | string | null> {
  const out: Record<string, number | string | null> = {};
  for (const key of nulls) out[key] = null;
  for (const [key, value] of Object.entries(draft)) {
    const def = schema.find((s) => s.key === key);
    out[key] = def?.choices.length ? String(value) : Number(value);
  }
  return out;
}

function useDraft() {
  const [draft, setDraft] = useState<Values>({});
  const [nulls, setNulls] = useState<Set<string>>(new Set());
  const change = (key: string, value: string | null) => {
    const next = { ...draft };
    const n = new Set(nulls);
    if (value === null) { delete next[key]; n.add(key); } else { next[key] = value; n.delete(key); }
    setDraft(next); setNulls(n);
  };
  return { draft, nulls, change, reset: () => { setDraft({}); setNulls(new Set()); } };
}

function apiErrors(err: unknown): string {
  const details = (err as { response?: { data?: { error?: { details?: Record<string, string> } } } }).response?.data?.error?.details;
  return details ? Object.entries(details).map(([k, v]) => `${k}: ${String(v)}`).join("; ") : "";
}

function OrganizationSettings() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const canManage = hasPermission(useAuth(), "settings.manage");
  const q = useApiQuery<OrgBody>("/settings/organization/");
  const { draft, nulls, change, reset } = useDraft();
  const [org, setOrg] = useState<Partial<OrgBody["organization"]>>({});
  const [saving, setSaving] = useState(false);
  useEffect(() => { if (q.data) setOrg({}); }, [q.data]);
  if (q.isLoading) return <SkeletonRows rows={6} />;
  if (q.isError || !q.data) return <ErrorState message={errorMessage(q.error, t("errors.generic"), t("errors.network"))} onRetry={() => void q.refetch()} />;
  const current = { ...q.data.organization, ...org };
  const save = async () => {
    setSaving(true);
    try {
      await api.patch("/settings/organization/", { ...org, values: toPayload(q.data.schema, draft, nulls) });
      toast.success(t("settings.saved")); reset(); setOrg({});
      await qc.invalidateQueries({ predicate: (k) => String(k.queryKey[1]).startsWith("/settings/") });
    } catch (err) { toast.error(apiErrors(err) || errorMessage(err, t("errors.generic"), t("errors.network"))); }
    finally { setSaving(false); }
  };
  return (
    <form onSubmit={(e) => { e.preventDefault(); void save(); }} className="space-y-6">
      <fieldset disabled={!canManage} className="space-y-6">
        <div className="grid gap-4 rounded-md border border-line bg-panel p-4 sm:grid-cols-3">
          <Field id="set-tz" label={t("settings.timezone")}>
            <Select id="set-tz" value={current.timezone} onChange={(e) => setOrg({ ...org, timezone: e.target.value })}>
              {[...new Set([current.timezone, ...TIMEZONES])].map((z) => <option key={z} value={z}>{z}</option>)}
            </Select></Field>
          <Field id="set-lang" label={t("settings.language")}>
            <Select id="set-lang" value={current.language} onChange={(e) => setOrg({ ...org, language: e.target.value })}>
              <option value="en">English</option><option value="sw">Kiswahili</option></Select></Field>
          <Field id="set-units" label={t("settings.units")}>
            <Select id="set-units" value={current.distance_unit} onChange={(e) => setOrg({ ...org, distance_unit: e.target.value })}>
              <option value="km">km</option><option value="mi">mi</option></Select></Field>
        </div>
        <SettingsFields schema={q.data.schema} draft={draft} inherited={q.data.defaults} overrides={Object.fromEntries(Object.entries(q.data.overrides).filter(([k]) => !nulls.has(k)))} onChange={change} idPrefix="org" />
      </fieldset>
      {canManage && <Button type="submit" busy={saving}>{t("common.save")}</Button>}
    </form>
  );
}

function PlatformSettings() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useApiQuery<PlatformBody>("/settings/platform/");
  const { draft, nulls, change, reset } = useDraft();
  const [fields, setFields] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  if (q.isLoading) return <SkeletonRows rows={4} />;
  if (q.isError || !q.data) return <ErrorState message={t("errors.generic")} onRetry={() => void q.refetch()} />;
  const value = (k: keyof PlatformBody) => fields[k] ?? String(q.data[k] ?? "");
  const save = async () => {
    setSaving(true);
    try {
      await api.patch("/settings/platform/", { ...fields, values: toPayload(q.data.schema, draft, nulls) });
      toast.success(t("settings.saved")); reset(); setFields({});
      await qc.invalidateQueries({ predicate: (k) => String(k.queryKey[1]).startsWith("/settings/") });
    } catch (err) { toast.error(apiErrors(err) || errorMessage(err, t("errors.generic"), t("errors.network"))); }
    finally { setSaving(false); }
  };
  return (
    <form onSubmit={(e) => { e.preventDefault(); void save(); }} className="space-y-6">
      <div className="grid gap-4 rounded-md border border-line bg-panel p-4 sm:grid-cols-2">
        <Field id="pl-tiles" label={t("settings.tileUrl")} hint={t("settings.tileHint")}>
          <TextInput id="pl-tiles" value={value("map_tile_url")} placeholder={q.data.env_map_tile_url} onChange={(e) => setFields({ ...fields, map_tile_url: e.target.value })} /></Field>
        <Field id="pl-attr" label={t("settings.attribution")}>
          <TextInput id="pl-attr" value={value("map_attribution")} onChange={(e) => setFields({ ...fields, map_attribution: e.target.value })} /></Field>
        <Field id="pl-geo" label={t("settings.geocoder")}>
          <Select id="pl-geo" value={value("geocoder_provider")} onChange={(e) => setFields({ ...fields, geocoder_provider: e.target.value })}>
            <option value="none">—</option><option value="nominatim">Nominatim</option><option value="custom">Custom</option></Select></Field>
        <Field id="pl-geourl" label={t("settings.geocoderUrl")}>
          <TextInput id="pl-geourl" value={value("geocoder_url")} onChange={(e) => setFields({ ...fields, geocoder_url: e.target.value })} /></Field>
      </div>
      <SettingsFields schema={q.data.schema} draft={draft} inherited={q.data.builtin} overrides={Object.fromEntries(Object.entries(q.data.overrides).filter(([k]) => !nulls.has(k)))} onChange={change} idPrefix="pl" />
      <Button type="submit" busy={saving}>{t("common.save")}</Button>
    </form>
  );
}

export function SettingsPage() {
  const { t } = useTranslation();
  const auth = useAuth();
  const canOrg = hasPermission(auth, "settings.view");
  const isRoot = !!auth.user?.is_platform_admin;
  const [tab, setTab] = useState<"org" | "platform">(canOrg ? "org" : "platform");
  return (
    <>
      <PageHeader title={t("nav.settings")} lede={t(tab === "org" ? "settings.lede" : "settings.platformLede")} />
      {canOrg && isRoot && (
        <div role="tablist" className="mb-4 flex gap-2">
          {(["org", "platform"] as const).map((k) => (
            <Button key={k} role="tab" aria-selected={tab === k} variant={tab === k ? "primary" : "secondary"} onClick={() => setTab(k)}>
              {t(k === "org" ? "settings.organization" : "settings.platform")}</Button>))}
        </div>)}
      {tab === "org" && canOrg ? <OrganizationSettings /> : isRoot ? <PlatformSettings /> : null}
    </>
  );
}
