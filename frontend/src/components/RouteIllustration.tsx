/**
 * Schematic route with stops: an illustration, never real fleet data (spec A12 landing page).
 * One orchestrated motion: the vehicle travels the route once; reduced-motion users see it parked.
 */
export function RouteIllustration({ label }: { label: string }) {
  const reduceMotion = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const path = "M40 300 C 120 300, 140 220, 220 210 S 330 120, 400 140 S 520 220, 600 90";
  return (
    <svg viewBox="0 0 640 360" role="img" aria-label={label} className="h-auto w-full">
      <defs>
        <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
          <path d="M40 0H0V40" fill="none" stroke="#d3dad8" strokeWidth="1" />
        </pattern>
      </defs>
      <rect width="640" height="360" rx="10" fill="#f7f9f8" />
      <rect width="640" height="360" rx="10" fill="url(#grid)" opacity="0.7" />
      <path d={path} fill="none" stroke="#17323a" strokeWidth="18" strokeLinecap="round" />
      <path d={path} fill="none" stroke="#e8a317" strokeWidth="3" strokeDasharray="14 12" strokeLinecap="round" />
      {[[40, 300], [220, 210], [400, 140]].map(([x, y], i) => (
        <g key={i}>
          <circle cx={x} cy={y} r="11" fill="#ffffff" stroke="#17323a" strokeWidth="3" />
          <circle cx={x} cy={y} r="4" fill="#17323a" />
        </g>
      ))}
      <g className="vehicle" transform={reduceMotion ? "translate(400 140)" : undefined}>
        <circle r="16" fill="#2e7d4f" stroke="#ffffff" strokeWidth="4" />
        <path d="M-5 5 L0 -7 L5 5 L0 2 Z" fill="#ffffff" />
        {!reduceMotion && <animateMotion dur="9s" fill="freeze" rotate="auto" path={path} keyPoints="0;1" keyTimes="0;1" calcMode="spline" keySplines="0.4 0 0.2 1" />}
      </g>
      <g transform="translate(600 90)">
        <circle r="12" fill="#e8a317" stroke="#17323a" strokeWidth="3" />
      </g>
    </svg>
  );
}
