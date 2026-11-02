import type { TFunction } from "i18next";

/** Alert text in the reader's language, from the alert's type and parameters (spec A11). Falls back to the
 *  server's English message for anything unknown. */
export function alertText(t: TFunction, a: { type: string; message: string; message_params?: Record<string, unknown> }): string {
  const p = { ...(a.message_params ?? {}) } as Record<string, unknown>;
  if (typeof p.reason === "string") p.reason = t(`alerts.reason.${p.reason}`);
  if (typeof p.state === "string") p.state = t(`alerts.state.${p.state}`, { date: p.date, defaultValue: p.state });
  if (typeof p.detail === "string") p.detail = t(`alerts.detail.${String(p.detail).replace(/ /g, "_")}`, { defaultValue: p.detail });
  const key = `alerts.msg.${a.type}`;
  const text = t(key, { ...p, defaultValue: "" });
  return text && !text.includes("{{") ? text : a.message;
}
