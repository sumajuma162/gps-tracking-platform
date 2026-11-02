import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { useTranslation } from "react-i18next";

/**
 * Accessible modal dialog.
 *
 * - Opened with ``showModal()``: the browser traps focus, shows a backdrop, closes on Esc and makes the page
 *   behind it inert, so nothing behind an open dialog can be clicked (this prevents racing actions).
 * - Rendered in a portal on <body>, so dialogs opened from a table row are not part of that row's content.
 */
export function Dialog({ open, title, onClose, children, wide }: {
  open: boolean; title: string; onClose: () => void; children: ReactNode; wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const { t } = useTranslation();

  useEffect(() => {
    const el = ref.current;
    if (!open || !el) return;
    if (typeof el.showModal === "function") {
      if (!el.open) el.showModal();
    } else {
      el.setAttribute("open", ""); // test environments without <dialog> support
    }
    return () => {
      if (typeof el.close === "function" && el.open) el.close();
    };
  }, [open]);

  if (!open) return null;
  const width = wide ? "w-[min(48rem,calc(100vw-2rem))]" : "w-[min(32rem,calc(100vw-2rem))]";
  return createPortal(
    <dialog ref={ref} aria-labelledby="dialog-title"
      onCancel={(e) => { e.preventDefault(); onClose(); }}
      className={`m-auto ${width} max-h-[calc(100vh-2rem)] overflow-y-auto rounded-lg border border-line bg-panel p-0 text-ink shadow-xl backdrop:bg-ink/40`}>
      <div className="flex items-center justify-between border-b border-line px-5 py-3">
        <h2 id="dialog-title" className="text-lg font-bold">{title}</h2>
        <button type="button" onClick={onClose} className="rounded p-1 hover:bg-paper" aria-label={t("common.close")}><X className="h-5 w-5" /></button>
      </div>
      <div className="px-5 py-4">{children}</div>
    </dialog>,
    document.body,
  );
}
