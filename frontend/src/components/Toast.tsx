import { create } from "zustand";
import { CheckCircle2, XCircle } from "lucide-react";

interface Toast { id: number; message: string; tone: "success" | "error" }
interface ToastState { toasts: Toast[]; push: (message: string, tone?: Toast["tone"]) => void; dismiss: (id: number) => void }

let nextId = 1;
export const useToasts = create<ToastState>((set, get) => ({
  toasts: [],
  push: (message, tone = "success") => {
    const id = nextId++;
    set({ toasts: [...get().toasts, { id, message, tone }] });
    window.setTimeout(() => get().dismiss(id), 5000);
  },
  dismiss: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),
}));

export const toast = {
  success: (m: string) => useToasts.getState().push(m, "success"),
  error: (m: string) => useToasts.getState().push(m, "error"),
};

export function Toaster() {
  const toasts = useToasts((s) => s.toasts);
  return (
    <div aria-live="polite" className="pointer-events-none fixed bottom-4 right-4 z-50 flex flex-col gap-2">
      {toasts.map((t) => (
        <div key={t.id} role="status"
          className="pointer-events-auto flex items-center gap-2 rounded-md border border-line bg-panel px-4 py-3 shadow-lg">
          {t.tone === "success" ? <CheckCircle2 className="h-5 w-5 text-moving" aria-hidden /> : <XCircle className="h-5 w-5 text-critical" aria-hidden />}
          <span>{t.message}</span>
        </div>
      ))}
    </div>
  );
}
