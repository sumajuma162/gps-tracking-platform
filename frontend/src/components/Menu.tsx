import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { MoreHorizontal } from "lucide-react";

export interface MenuItem { label: string; onSelect: () => void; danger?: boolean }

/** Small accessible overflow menu: Esc or an outside click closes it, focus returns to the trigger. */
export function Menu({ label, items, icon }: { label: string; items: MenuItem[]; icon?: ReactNode }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const outside = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); trigger.current?.focus(); } };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", outside); document.removeEventListener("keydown", esc); };
  }, [open]);
  if (!items.length) return null;
  return (
    <div ref={root} className="relative inline-block">
      <button ref={trigger} type="button" aria-label={label} aria-haspopup="menu" aria-expanded={open} aria-controls={id}
        onClick={() => setOpen((o) => !o)} className="rounded-md p-1.5 hover:bg-paper">
        {icon ?? <MoreHorizontal className="h-5 w-5" aria-hidden />}
      </button>
      {open && (
        <ul id={id} role="menu" className="absolute right-0 z-30 mt-1 min-w-36 rounded-md border border-line bg-panel py-1 shadow-lg">
          {items.map((item) => (
            <li key={item.label} role="none">
              <button type="button" role="menuitem" onClick={() => { setOpen(false); item.onSelect(); }}
                className={`block w-full whitespace-nowrap px-4 py-2 text-left text-sm hover:bg-paper ${item.danger ? "text-critical" : ""}`}>
                {item.label}
              </button>
            </li>))}
        </ul>)}
    </div>
  );
}
