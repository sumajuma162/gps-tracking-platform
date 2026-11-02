import { useEffect, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { api, errorMessage } from "../../services/api";
import { useApiQuery } from "../../services/hooks";
import { Button, EmptyState, ErrorState, Field, PageHeader, Select, TextInput } from "../../components/ui";
import { createMap } from "../../maps";
import type { MapAdapter } from "../../maps/types";
import { toast } from "../../components/Toast";
import { useAuth } from "../../store/auth";

interface ManualOptions {
  organizations: { id: number; name: string }[];
  devices: { id: number; organization_id: number; organization_name: string; name: string;
    identifier: string; protocol: string; vehicle: string }[];
}
interface ManualResult {
  device_id: number; device_name: string; organization_name: string; vehicle: string;
  latitude: number; longitude: number; device_time: string; accepted_records: number;
}
interface MapSettings { tile_url: string; attribution: string; center: [number, number]; zoom: number }

export function ManualPositionPage() {
  const { t } = useTranslation();
  const isPlatformAdmin = useAuth((state) => !!state.user?.is_platform_admin);
  const endpoint = isPlatformAdmin ? "/platform/manual-positions/" : "/tracking/manual-positions/";
  const options = useApiQuery<ManualOptions>(endpoint);
  const mapSettings = useApiQuery<MapSettings>("/settings/map/");
  const [organizationId, setOrganizationId] = useState("");
  const [deviceId, setDeviceId] = useState("");
  const [latitude, setLatitude] = useState("-6.8235");
  const [longitude, setLongitude] = useState("39.2695");
  const [speed, setSpeed] = useState("35");
  const [heading, setHeading] = useState("90");
  const [ignition, setIgnition] = useState(true);
  const [result, setResult] = useState<ManualResult | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const mapElement = useRef<HTMLDivElement>(null);
  const map = useRef<MapAdapter | null>(null);
  const orgId = organizationId || String(options.data?.organizations[0]?.id ?? "");
  const devices = options.data?.devices.filter((device) => String(device.organization_id) === orgId) ?? [];
  const selectedDeviceId = devices.some((device) => String(device.id) === deviceId) ? deviceId : String(devices[0]?.id ?? "");

  useEffect(() => {
    if (!mapElement.current || !mapSettings.data || map.current) return;
    map.current = createMap(mapElement.current, { tileUrl: mapSettings.data.tile_url,
      attribution: mapSettings.data.attribution, center: mapSettings.data.center, zoom: mapSettings.data.zoom });
    return () => { map.current?.destroy(); map.current = null; };
  }, [mapSettings.data, options.data]);

  useEffect(() => {
    if (!result || !map.current) return;
    const position = { lat: result.latitude, lon: result.longitude };
    map.current.setMarkers([{ id: String(result.device_id), ...position, heading: Number(heading) || 0,
      status: Number(speed) > 5 ? "ONLINE_MOVING" : "ONLINE_STOPPED", label: `${result.vehicle}: ${result.device_name}` }]);
    map.current.panTo(position);
  }, [result, heading, speed, mapSettings.data]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedDeviceId) return;
    setBusy(true);
    setSubmitError(null);
    try {
      const { data } = await api.post<ManualResult>(endpoint, {
        device_id: Number(selectedDeviceId), latitude: Number(latitude), longitude: Number(longitude),
        speed_kmh: Number(speed), heading: heading === "" ? null : Number(heading), ignition,
      });
      setResult(data);
      toast.success(t("manualPosition.submitted"));
    } catch (error) {
      setSubmitError(errorMessage(error, t("errors.generic"), t("errors.network")));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader title={t("manualPosition.title")} lede={t("manualPosition.lede")} />
      {options.isLoading ? <p>{t("common.loading")}</p> : options.isError ?
        <ErrorState message={errorMessage(options.error, t("errors.generic"), t("errors.network"))} /> :
        !options.data?.devices.length ? <EmptyState message={t("manualPosition.empty")} /> : (
          <div className="grid gap-6 xl:grid-cols-[minmax(20rem,28rem)_1fr]">
            <form onSubmit={(event) => void submit(event)} className="space-y-4 rounded-md border border-line bg-panel p-5">
              {submitError && <ErrorState message={submitError} />}
              <Field id="manual-org" label={t("manualPosition.organization")}>
                <Select id="manual-org" value={orgId} onChange={(event) => { setOrganizationId(event.target.value); setDeviceId(""); }}>
                  {options.data.organizations.map((org) => <option key={org.id} value={org.id}>{org.name}</option>)}
                </Select>
              </Field>
              <Field id="manual-device" label={t("manualPosition.tracker")}>
                <Select id="manual-device" value={selectedDeviceId} onChange={(event) => setDeviceId(event.target.value)}>
                  {devices.map((device) => <option key={device.id} value={device.id}>{device.vehicle} · {device.name} ({device.identifier})</option>)}
                </Select>
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field id="manual-lat" label={t("manualPosition.latitude")}>
                  <TextInput id="manual-lat" type="number" min="-90" max="90" step="any" required value={latitude} onChange={(event) => setLatitude(event.target.value)} />
                </Field>
                <Field id="manual-lon" label={t("manualPosition.longitude")}>
                  <TextInput id="manual-lon" type="number" min="-180" max="180" step="any" required value={longitude} onChange={(event) => setLongitude(event.target.value)} />
                </Field>
                <Field id="manual-speed" label={t("manualPosition.speed")}>
                  <TextInput id="manual-speed" type="number" min="0" max="300" step="any" required value={speed} onChange={(event) => setSpeed(event.target.value)} />
                </Field>
                <Field id="manual-heading" label={t("manualPosition.heading")}>
                  <TextInput id="manual-heading" type="number" min="0" max="359.999" step="any" value={heading} onChange={(event) => setHeading(event.target.value)} />
                </Field>
              </div>
              <label className="flex items-center gap-2 text-sm font-medium">
                <input type="checkbox" checked={ignition} onChange={(event) => setIgnition(event.target.checked)} />
                {t("manualPosition.ignition")}
              </label>
              <Button type="submit" busy={busy} disabled={!selectedDeviceId}>{t("manualPosition.submit")}</Button>
            </form>
            <section className="min-h-96 overflow-hidden rounded-md border border-line bg-panel" aria-label={t("manualPosition.map")}>
              {result && <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line p-4">
                <div><h2 className="font-semibold">{result.vehicle} · {result.device_name}</h2>
                  <p className="font-mono text-sm text-ink-soft">{result.latitude.toFixed(6)}, {result.longitude.toFixed(6)}</p></div>
                <a className="text-sm font-semibold underline" href={`https://www.google.com/maps?q=${result.latitude},${result.longitude}`} target="_blank" rel="noreferrer">{t("manualPosition.map")}</a>
              </div>}
              <div className="relative h-[28rem] w-full bg-[#e9eeec]">
                <div ref={mapElement} className="h-full w-full" />
                {!result && <p className="absolute inset-0 grid place-items-center bg-panel/80 p-8 text-center text-ink-soft">{t("manualPosition.empty")}</p>}
              </div>
            </section>
          </div>
        )}
    </>
  );
}