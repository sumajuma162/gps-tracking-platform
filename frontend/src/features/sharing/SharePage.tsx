import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import axios from "axios";
import { createMap } from "../../maps";
import type { MapAdapter } from "../../maps/types";
import { formatDateTime } from "../../components/ui";

interface Shared { vehicle: string; label: string; expires_at: string;
  position?: { lat: number; lon: number; heading: number | null; speed_kmh: number | null; status: string; updated_at: string } | null;
  trip?: { start: string; end: string; distance_km: number; points: [number, number][] } }

/** Public share link (spec A13): one vehicle's position or one trip, nothing else. No sign-in. */
export function SharePage() {
  const { t } = useTranslation();
  const { token = "" } = useParams();
  const [data, setData] = useState<Shared | null>(null);
  const [gone, setGone] = useState(false);
  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<MapAdapter | null>(null);

  useEffect(() => {
    let timer: number | undefined;
    const load = async () => {
      try {
        const { data: body } = await axios.get<Shared>(`/api/v1/public/share/${encodeURIComponent(token)}/`);
        setData(body);
        if (!body.trip) timer = window.setTimeout(() => void load(), 15_000);   // live position refresh
      } catch { setGone(true); }
    };
    void load();
    return () => window.clearTimeout(timer);
  }, [token]);

  useEffect(() => {
    if (!data || !mapEl.current) return;
    if (!map.current) map.current = createMap(mapEl.current, { tileUrl: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
      attribution: "© OpenStreetMap contributors", center: [-6.79, 39.21], zoom: 12 });
    const m = map.current;
    if (data.trip && data.trip.points.length > 1) {
      const pts = data.trip.points.map(([lat, lon]) => ({ lat, lon }));
      m.setRoute(pts, { arrows: true });
      m.fitTo(pts, 16);
    } else if (data.position) {
      m.setMarkers([{ id: "shared", lat: data.position.lat, lon: data.position.lon, heading: data.position.heading,
        status: data.position.status, label: data.vehicle }]);
      m.panTo({ lat: data.position.lat, lon: data.position.lon });
    }
  }, [data]);
  useEffect(() => () => { map.current?.destroy(); map.current = null; }, []);

  return (
    <main className="mx-auto max-w-4xl p-4">
      <h1 className="font-display text-2xl font-bold">{data ? data.vehicle : t("share.title")}</h1>
      {gone ? <p role="alert" className="mt-4">{t("share.gone")}</p> : (
        <>
          {data && <p className="mb-3 text-sm text-ink-soft" data-testid="share-meta">
            {data.label ? `${data.label} · ` : ""}
            {data.trip ? t("share.trip", { start: formatDateTime(data.trip.start), km: data.trip.distance_km.toFixed(1) })
              : data.position ? t("share.updated", { when: formatDateTime(data.position.updated_at) }) : t("share.noPosition")}
            {" · "}{t("share.expires", { when: formatDateTime(data.expires_at) })}</p>}
          <div ref={mapEl} className="h-[70vh] w-full rounded-lg border border-line bg-[#e9eeec]" data-testid="share-map" aria-label={t("share.map")} />
        </>)}
    </main>
  );
}
