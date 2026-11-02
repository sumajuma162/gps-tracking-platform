/** Map abstraction (spec A12): pages talk to this interface; Leaflet is one implementation, MapLibre can be another. */
export interface LatLon { lat: number; lon: number }
export type Bounds = [south: number, west: number, north: number, east: number];

export interface MarkerSpec extends LatLon {
  id: string;
  status: string;
  heading?: number | null;
  label: string;
  selected?: boolean;
}

export interface MapOptions {
  tileUrl: string;
  attribution: string;
  center: [number, number];
  zoom: number;
  cluster?: boolean;
  interactive?: boolean;
}

export interface PinSpec extends LatLon { id: string; kind: "start" | "end" | "stop" | "event"; label: string }

/** A geofence-like shape: a circle (center + radius) or a polygon ring. */
export interface ShapeSpec {
  id: string; kind: "circle" | "polygon"; color: string; label: string;
  center?: LatLon; radius_m?: number; polygon?: LatLon[]; dashed?: boolean;
}

export interface MapAdapter {
  setMarkers(markers: MarkerSpec[]): void;
  /** One route line (replaces any previous one); null removes it. ``highlights`` are stretches drawn in a warning
   *  colour (e.g. speeding); ``arrows`` adds direction chevrons along the line. */
  setRoute(points: LatLon[] | null, options?: { highlights?: LatLon[][]; arrows?: boolean }): void;
  /** Geofences and drafts. Replaces the previous set. */
  setShapes(shapes: ShapeSpec[]): void;
  /** Clicks on empty map (for drawing); null stops listening. */
  onMapClick(handler: ((point: LatLon) => void) | null): void;
  /** Extra-detail line segments for the zoomed-in area, drawn over the route. */
  setDetail(segments: LatLon[][]): void;
  /** Start / end / stop pins along a route. */
  setPins(pins: PinSpec[]): void;
  /** The playback marker; called on every animation frame, so it must be cheap. null hides it. */
  setCursor(cursor: (LatLon & { heading?: number | null }) | null): void;
  fitTo(points: LatLon[], maxZoom?: number): void;
  panTo(point: LatLon): void;
  onMarkerClick(handler: (id: string) => void): void;
  onViewChange(handler: (bounds: Bounds, zoom: number) => void): void;
  resize(): void;
  destroy(): void;
}

export type MapFactory = (element: HTMLElement, options: MapOptions) => MapAdapter;

export const STATUS_COLORS: Record<string, string> = {
  ONLINE_MOVING: "#2e7d4f", ONLINE_IDLE: "#b7791f", ONLINE_STOPPED: "#4b6168", NO_GPS: "#c2410c",
  OFFLINE: "#7d878b", UNKNOWN: "#a8b1b4",
};
