/** Route playback maths, kept pure for testing. A route point is [epoch_s, lat, lon, speed_kmh, heading, ignition]. */
export type RoutePoint = [number, number, number, number, number | null, boolean | null];

export interface Sample { t: number; lat: number; lon: number; speed: number; heading: number | null; index: number }

/** Spec A12 speeds (0.5x-16x), plus 60x and 300x for long ranges. */
export const RATES = [0.5, 1, 2, 4, 8, 16, 60, 300] as const;

/** Index of the last point at or before ``t`` (binary search). */
export function indexAt(points: RoutePoint[], t: number): number {
  let lo = 0, hi = points.length - 1;
  if (!points.length || t <= points[0][0]) return 0;
  if (t >= points[hi][0]) return hi;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (points[mid][0] <= t) lo = mid; else hi = mid - 1;
  }
  return lo;
}

/** Interpolated position at time ``t``; the speed and heading are those of the segment's first point. */
export function sampleAt(points: RoutePoint[], t: number): Sample | null {
  if (!points.length) return null;
  const i = indexAt(points, t);
  const a = points[i];
  const b = points[Math.min(i + 1, points.length - 1)];
  const span = b[0] - a[0];
  const f = span > 0 ? Math.min(1, Math.max(0, (t - a[0]) / span)) : 0;
  return { t, lat: a[1] + (b[1] - a[1]) * f, lon: a[2] + (b[2] - a[2]) * f, speed: a[3], heading: a[4], index: i };
}

/** Route time reached after ``elapsedMs`` of real time at ``rate``x, never past the end. */
export function advance(t: number, elapsedMs: number, rate: number, end: number): number {
  return Math.min(end, t + (elapsedMs / 1000) * rate);
}

export function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min`;
}
