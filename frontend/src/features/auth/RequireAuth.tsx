import { useEffect, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { refreshSession } from "../../services/api";
import { useAuth } from "../../store/auth";
import { SkeletonRows } from "../../components/ui";

/** Restores the session from the refresh cookie on first load; sends anonymous users to sign in. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const status = useAuth((s) => s.status);
  const location = useLocation();
  useEffect(() => { if (status === "unknown") void refreshSession(); }, [status]);
  if (status === "unknown") return <div className="mx-auto max-w-md p-12"><SkeletonRows rows={3} /></div>;
  if (status === "anonymous") {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  return <>{children}</>;
}

/** Only allow same-origin relative redirects after sign-in (prevents open redirects). */
/** Only same-origin paths: one leading slash, no backslashes, whitespace or control characters (browsers drop
 *  tabs and newlines, so "/\t/evil.example" would become "//evil.example"), no scheme. */
export function safeNext(raw: string | null): string {
  // eslint-disable-next-line no-control-regex
  if (!raw || !/^\/(?![/\\])[^\s\\\u0000-\u001f]*$/.test(raw) || /^\/[^/?#]*:/.test(raw)) return "/app";
  return raw;
}
