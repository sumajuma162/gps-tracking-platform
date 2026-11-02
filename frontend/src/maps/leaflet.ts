import L from "leaflet";
import "leaflet.markercluster";
import "leaflet/dist/leaflet.css";
import "leaflet.markercluster/dist/MarkerCluster.css";
import type { Bounds, LatLon, MapAdapter, MapFactory, MarkerSpec, PinSpec, ShapeSpec } from "./types";
import { STATUS_COLORS } from "./types";

const ANIMATE_MS = 800;

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] ?? c);
}

/** Arrow when moving (rotated to heading), dot otherwise; colour by status. Labels are escaped. */
function icon(m: MarkerSpec): L.DivIcon {
  const color = STATUS_COLORS[m.status] ?? STATUS_COLORS.UNKNOWN;
  const moving = m.status === "ONLINE_MOVING" && m.heading !== null && m.heading !== undefined;
  const ring = m.selected ? `<circle r="15" fill="none" stroke="#e8a317" stroke-width="4"/>` : "";
  const shape = moving
    ? `<g transform="rotate(${Math.round(m.heading ?? 0)})"><path d="M0,-12 L9,10 L0,5 L-9,10 Z" fill="${color}" stroke="#fff" stroke-width="2"/></g>`
    : `<circle r="8" fill="${color}" stroke="#fff" stroke-width="3"/>`;
  return L.divIcon({
    className: "fleet-marker",
    iconSize: [34, 34],
    iconAnchor: [17, 17],
    html: `<svg width="34" height="34" viewBox="-17 -17 34 34" role="img" aria-label="${escapeHtml(m.label)}"
      data-device-id="${escapeHtml(m.id)}" data-status="${m.status}" data-lat="${m.lat.toFixed(6)}" data-lon="${m.lon.toFixed(6)}">${ring}${shape}</svg>`,
  });
}

export const createLeafletMap: MapFactory = (element, options) => {
  const interactive = options.interactive ?? true;
  // maxZoom on the map itself: clustering needs it, and it must not depend on a tile layer being configured.
  const map = L.map(element, { maxZoom: 19, minZoom: 3, zoomControl: interactive, attributionControl: true, dragging: interactive,
    scrollWheelZoom: interactive, doubleClickZoom: interactive, keyboard: interactive }).setView(options.center, options.zoom);
  if (options.tileUrl) {
    L.tileLayer(options.tileUrl, { attribution: options.attribution, maxZoom: 19 }).addTo(map);
  } else {
    element.classList.add("map-no-tiles");
  }
  const layer: L.LayerGroup = options.cluster
    ? L.markerClusterGroup({ showCoverageOnHover: false, maxClusterRadius: 50 })
    : L.layerGroup();
  layer.addTo(map);
  const markers = new Map<string, { marker: L.Marker; spec: MarkerSpec; frame?: number }>();
  const renderer = L.canvas({ padding: 0.5 });   // canvas keeps thousands of route points fast
  let route: L.Polyline | null = null;
  const routeExtras = L.layerGroup().addTo(map);
  const detailLayer = L.layerGroup().addTo(map);
  const shapeLayer = L.layerGroup().addTo(map);
  let mapClick: ((p: LatLon) => void) | null = null;
  map.on("click", (e: L.LeafletMouseEvent) => mapClick?.({ lat: e.latlng.lat, lon: e.latlng.lng }));
  const pinLayer = L.layerGroup().addTo(map);
  let cursor: L.Marker | null = null;
  let cursorHeading: number | null | undefined;
  let clickHandler: ((id: string) => void) | null = null;
  const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

  const animate = (entry: { marker: L.Marker; frame?: number }, from: L.LatLng, to: L.LatLng) => {
    if (entry.frame) cancelAnimationFrame(entry.frame);
    if (reduceMotion || from.distanceTo(to) > 5000) { entry.marker.setLatLng(to); return; }
    const start = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / ANIMATE_MS);
      entry.marker.setLatLng([from.lat + (to.lat - from.lat) * t, from.lng + (to.lng - from.lng) * t]);
      if (t < 1) entry.frame = requestAnimationFrame(step);
    };
    entry.frame = requestAnimationFrame(step);
  };

  const adapter: MapAdapter = {
    setMarkers(specs) {
      const keep = new Set(specs.map((s) => s.id));
      for (const [id, entry] of markers) {
        if (!keep.has(id)) { layer.removeLayer(entry.marker); markers.delete(id); }
      }
      for (const spec of specs) {
        const existing = markers.get(spec.id);
        const target = L.latLng(spec.lat, spec.lon);
        if (!existing) {
          const marker = L.marker(target, { icon: icon(spec), title: spec.label, keyboard: interactive });
          marker.on("click", () => clickHandler?.(spec.id));
          layer.addLayer(marker);
          markers.set(spec.id, { marker, spec });
          continue;
        }
        const prev = existing.spec;
        if (prev.status !== spec.status || prev.heading !== spec.heading || prev.selected !== spec.selected
            || prev.label !== spec.label || prev.lat !== spec.lat || prev.lon !== spec.lon) {
          existing.marker.setIcon(icon(spec));
        }
        if (prev.lat !== spec.lat || prev.lon !== spec.lon) animate(existing, existing.marker.getLatLng(), target);
        existing.spec = spec;
      }
    },
    setRoute(points, options = {}) {
      route?.remove();
      route = null;
      routeExtras.clearLayers();
      if (!points || points.length < 2) { delete element.dataset.routePoints; return; }
      route = L.polyline(points.map((p) => [p.lat, p.lon] as [number, number]),
        { renderer, color: "#14323b", weight: 4, opacity: 0.85, interactive: false }).addTo(map);
      element.dataset.routePoints = String(points.length);
      for (const stretch of options.highlights ?? []) {
        if (stretch.length > 1) L.polyline(stretch.map((p) => [p.lat, p.lon] as [number, number]),
          { renderer, color: "#c2410c", weight: 6, opacity: 0.95, interactive: false }).addTo(routeExtras);
      }
      element.dataset.routeHighlights = String((options.highlights ?? []).length);
      if (options.arrows) {               // ~30 chevrons spread along the route, pointing the way it was driven
        const every = Math.max(1, Math.floor(points.length / 30));
        for (let i = every; i < points.length - 1; i += every) {
          const a = points[i], b = points[i + 1];
          if (a.lat === b.lat && a.lon === b.lon) continue;
          const y = Math.sin((b.lon - a.lon) * Math.PI / 180) * Math.cos(b.lat * Math.PI / 180);
          const x = Math.cos(a.lat * Math.PI / 180) * Math.sin(b.lat * Math.PI / 180)
            - Math.sin(a.lat * Math.PI / 180) * Math.cos(b.lat * Math.PI / 180) * Math.cos((b.lon - a.lon) * Math.PI / 180);
          const bearing = (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
          L.marker([a.lat, a.lon], { interactive: false, keyboard: false, icon: L.divIcon({ className: "route-arrow", iconSize: [14, 14],
            iconAnchor: [7, 7], html: `<svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden="true"><path transform="rotate(${Math.round(bearing)})" d="M-4,3 L0,-4 L4,3" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>` }) })
            .addTo(routeExtras);
        }
      }
    },
    setShapes(shapes: ShapeSpec[]) {
      shapeLayer.clearLayers();
      for (const sh of shapes) {
        const style = { color: sh.color, weight: 2, fillOpacity: 0.15, dashArray: sh.dashed ? "6 6" : undefined, interactive: false };
        const layer = sh.kind === "circle" && sh.center && sh.radius_m
          ? L.circle([sh.center.lat, sh.center.lon], { ...style, radius: sh.radius_m })
          : sh.polygon && sh.polygon.length > 1 ? L.polygon(sh.polygon.map((p) => [p.lat, p.lon] as [number, number]), style) : null;
        if (!layer) continue;
        layer.addTo(shapeLayer);
        if (sh.label) layer.bindTooltip(escapeHtml(sh.label), { sticky: true });
        for (const p of sh.kind === "polygon" ? sh.polygon ?? [] : []) {
          if (sh.dashed) L.circleMarker([p.lat, p.lon], { radius: 4, color: sh.color, fillOpacity: 1, interactive: false }).addTo(shapeLayer);
        }
      }
      element.dataset.shapes = String(shapes.length);
    },
    onMapClick(handler) { mapClick = handler; element.style.cursor = handler ? "crosshair" : ""; },
    setDetail(segments) {
      detailLayer.clearLayers();
      for (const seg of segments) {
        if (seg.length > 1) L.polyline(seg.map((p) => [p.lat, p.lon] as [number, number]),
          { renderer, color: "#0b1d22", weight: 2, opacity: 0.9, interactive: false }).addTo(detailLayer);
      }
      element.dataset.detailSegments = String(segments.length);
    },
    setPins(pins: PinSpec[]) {
      pinLayer.clearLayers();
      for (const pin of pins) {
        const color = { start: "#2e7d4f", end: "#b3261e", stop: "#e8a317", event: "#2563eb" }[pin.kind];
        const text = { start: "A", end: "B", stop: "P", event: "G" }[pin.kind];
        L.marker([pin.lat, pin.lon], { title: pin.label, keyboard: interactive, icon: L.divIcon({ className: "fleet-marker",
          iconSize: [26, 26], iconAnchor: [13, 13],
          html: `<svg width="26" height="26" viewBox="-13 -13 26 26" role="img" aria-label="${escapeHtml(pin.label)}" data-pin="${pin.kind}">
            <circle r="11" fill="${color}" stroke="#fff" stroke-width="2.5"/><text text-anchor="middle" dy="4.5" font-size="12" font-weight="700" fill="#fff">${text}</text></svg>` }) })
          .addTo(pinLayer);
      }
    },
    setCursor(c) {
      if (!c) { cursor?.remove(); cursor = null; return; }
      if (!cursor) {
        cursor = L.marker([c.lat, c.lon], { interactive: false, keyboard: false, zIndexOffset: 1000 }).addTo(map);
        cursorHeading = undefined;
      }
      cursor.setLatLng([c.lat, c.lon]);
      const heading = c.heading == null ? null : Math.round(c.heading / 5) * 5;   // re-render the icon only on real turns
      if (heading !== cursorHeading) {
        cursorHeading = heading;
        cursor.setIcon(icon({ id: "cursor", lat: c.lat, lon: c.lon, heading, status: "ONLINE_MOVING", label: "" }));
      }
      const el = cursor.getElement();
      if (el) { el.dataset.lat = c.lat.toFixed(6); el.dataset.lon = c.lon.toFixed(6); el.setAttribute("data-testid", "playback-cursor"); }
    },
    fitTo(points: LatLon[], maxZoom = 15) {
      if (!points.length) return;
      map.fitBounds(L.latLngBounds(points.map((p) => [p.lat, p.lon] as [number, number])), { padding: [40, 40], maxZoom });
    },
    panTo(point) { map.panTo([point.lat, point.lon], { animate: !reduceMotion }); },
    onMarkerClick(handler) { clickHandler = handler; },
    onViewChange(handler) {
      const emit = () => { const b = map.getBounds(); handler([b.getSouth(), b.getWest(), b.getNorth(), b.getEast()] as Bounds, map.getZoom()); };
      map.on("moveend", emit);
    },
    resize() { map.invalidateSize(); },
    destroy() { markers.forEach((e) => e.frame && cancelAnimationFrame(e.frame)); map.remove(); },
  };
  return adapter;
};
