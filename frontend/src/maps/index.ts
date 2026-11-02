/** The map engine used by every page. Swapping to MapLibre means changing this one export. */
export { createLeafletMap as createMap } from "./leaflet";
export type { MapAdapter, MapOptions, MarkerSpec } from "./types";
