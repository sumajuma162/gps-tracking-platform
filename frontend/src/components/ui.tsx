import { forwardRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";
import { AlertTriangle, Inbox, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

type Variant = "primary" | "secondary" | "danger" | "ghost";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-ink text-white hover:bg-ink-deep",
  secondary: "bg-panel text-ink border border-line hover:border-ink-soft",
  danger: "bg-critical text-white hover:brightness-95",
  ghost: "text-ink hover:bg-paper",
};

export const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean }>(
  function Button({ variant = "primary", busy, className = "", children, disabled, ...rest }, ref) {
    return (
      <button ref={ref} disabled={disabled || busy}
        className={`inline-flex items-center justify-center gap-2 rounded-md px-4 py-2 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${VARIANTS[variant]} ${className}`}
        {...rest}>
        {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
        {children}
      </button>
    );
  },
);

interface FieldProps { label: string; error?: string; hint?: string; id: string; children: ReactNode }
export function Field({ label, error, hint, id, children }: FieldProps) {
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-sm font-semibold">{label}</label>
      {children}
      {hint && !error && <p id={`${id}-hint`} className="text-xs text-ink-soft">{hint}</p>}
      {error && <p id={`${id}-error`} role="alert" className="text-sm font-medium text-critical">{error}</p>}
    </div>
  );
}

const inputClass = "w-full rounded-md border border-line bg-panel px-3 py-2 text-base outline-none focus:border-ink aria-[invalid=true]:border-critical";

export const TextInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }>(
  function TextInput({ invalid, id, ...rest }, ref) {
    return <input ref={ref} id={id} aria-invalid={invalid || undefined}
      aria-describedby={invalid ? `${id}-error` : `${id}-hint`} className={inputClass} {...rest} />;
  },
);

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  function Select(props, ref) { return <select ref={ref} className={inputClass} {...props} />; },
);

export function Badge({ tone = "neutral", children }: { tone?: "neutral" | "good" | "warn" | "bad" | "marking"; children: ReactNode }) {
  const tones = { neutral: "bg-paper text-ink-soft", good: "bg-moving/10 text-moving", warn: "bg-idle/10 text-idle",
    bad: "bg-critical/10 text-critical", marking: "bg-marking-soft text-ink" };
  return <span className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-semibold ${tones[tone]}`}>{children}</span>;
}

export function PageHeader({ title, lede, actions }: { title: string; lede?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-3xl font-bold">{title}</h1>
        {lede && <p className="mt-1 max-w-prose text-ink-soft">{lede}</p>}
      </div>
      {actions}
    </div>
  );
}

export function SkeletonRows({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-busy="true" aria-live="polite">
      {Array.from({ length: rows }, (_, i) => <div key={i} className="h-10 animate-pulse rounded bg-line/60" />)}
    </div>
  );
}

export function EmptyState({ message, action }: { message: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-md border border-dashed border-line px-6 py-12 text-center text-ink-soft">
      <Inbox className="h-8 w-8" aria-hidden />
      <p>{message}</p>
      {action}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  const { t } = useTranslation();
  return (
    <div role="alert" className="flex items-start gap-3 rounded-md border border-critical/30 bg-critical/5 p-4">
      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-critical" aria-hidden />
      <div className="space-y-2">
        <p>{message}</p>
        {onRetry && <Button variant="secondary" onClick={onRetry}>{t("common.retry")}</Button>}
      </div>
    </div>
  );
}

export function formatDateTime(value: string | null | undefined, timeZone?: string, never = "—"): string {
  if (!value) return never;
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone }).format(new Date(value));
  } catch {
    return new Date(value).toLocaleString();
  }
}
