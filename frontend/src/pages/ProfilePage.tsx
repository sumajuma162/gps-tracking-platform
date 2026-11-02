import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useQueryClient } from "@tanstack/react-query";
import { api, errorMessage, fieldErrors } from "../services/api";
import { useApiQuery } from "../services/hooks";
import { useAuth, type SessionPayload } from "../store/auth";
import { Badge, Button, ErrorState, Field, PageHeader, SkeletonRows, TextInput, formatDateTime } from "../components/ui";
import { toast } from "../components/Toast";
import { NotificationSettings } from "../features/alerts/NotificationSettings";

interface Session { id: string; created_at: string; last_used_at: string; ip_address: string | null; user_agent: string; current: boolean }

export function ProfilePage() {
  const { t } = useTranslation();
  return (
    <>
      <PageHeader title={t("profile.title")} />
      <div className="grid gap-6 lg:grid-cols-2">
        <DetailsForm />
        <PasswordForm />
        <div className="lg:col-span-2"><Sessions /></div>
      </div>
      <NotificationSettings />
    </>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="rounded-lg border border-line bg-panel p-5"><h2 className="mb-4 text-xl font-bold">{title}</h2>{children}</section>;
}

function DetailsForm() {
  const { t } = useTranslation();
  const user = useAuth((s) => s.user);
  const setSession = useAuth((s) => s.setSession);
  const schema = z.object({ full_name: z.string().trim().max(150) });
  const { register, handleSubmit, formState: { isSubmitting } } = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema), defaultValues: { full_name: user?.full_name ?? "" } });
  const onSubmit = handleSubmit(async (values) => {
    try { const { data } = await api.patch<SessionPayload>("/auth/me/", values); setSession(data); toast.success(t("profile.saved")); }
    catch (err) { toast.error(errorMessage(err, t("errors.generic"), t("errors.network"))); }
  });
  return (
    <Panel title={t("profile.details")}>
      <form onSubmit={onSubmit} className="space-y-4">
        <Field id="me-email" label={t("common.email")}><TextInput id="me-email" value={user?.email ?? ""} disabled readOnly /></Field>
        <Field id="me-name" label={t("users.fullName")}><TextInput id="me-name" {...register("full_name")} /></Field>
        <Button type="submit" busy={isSubmitting}>{t("common.save")}</Button>
      </form>
    </Panel>
  );
}

function PasswordForm() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const schema = z.object({
    current_password: z.string().min(1, t("auth.passwordRequired")),
    new_password: z.string().min(12, t("auth.tooShort")),
    confirm: z.string(),
  }).refine((v) => v.new_password === v.confirm, { path: ["confirm"], message: t("auth.mismatch") });
  const { register, handleSubmit, setError, reset, formState: { errors, isSubmitting } } = useForm<z.infer<typeof schema>>({ resolver: zodResolver(schema) });
  const onSubmit = handleSubmit(async ({ current_password, new_password }) => {
    try {
      await api.post("/auth/password/change/", { current_password, new_password });
      toast.success(t("profile.changed")); reset(); void qc.invalidateQueries({ predicate: (q) => q.queryKey[1] === "/auth/sessions/" });
    } catch (err) {
      const f = fieldErrors(err);
      if (f.new_password) setError("new_password", { message: f.new_password });
      else setError("current_password", { message: errorMessage(err, t("errors.generic"), t("errors.network")) });
    }
  });
  return (
    <Panel title={t("profile.passwordTitle")}>
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        <Field id="pw-current" label={t("profile.currentPassword")} error={errors.current_password?.message}>
          <TextInput id="pw-current" type="password" autoComplete="current-password" invalid={!!errors.current_password} {...register("current_password")} />
        </Field>
        <Field id="pw-new" label={t("auth.newPassword")} error={errors.new_password?.message} hint={t("auth.passwordRules")}>
          <TextInput id="pw-new" type="password" autoComplete="new-password" invalid={!!errors.new_password} {...register("new_password")} />
        </Field>
        <Field id="pw-confirm" label={t("auth.confirmPassword")} error={errors.confirm?.message}>
          <TextInput id="pw-confirm" type="password" autoComplete="new-password" invalid={!!errors.confirm} {...register("confirm")} />
        </Field>
        <Button type="submit" busy={isSubmitting}>{t("profile.passwordTitle")}</Button>
      </form>
    </Panel>
  );
}

function Sessions() {
  const { t } = useTranslation();
  const q = useApiQuery<Session[]>("/auth/sessions/");
  const [busy, setBusy] = useState(false);
  const revoke = async (id?: string) => {
    setBusy(true);
    try {
      if (id) await api.delete(`/auth/sessions/${id}/`);
      else { const { data } = await api.post<{ ended: number }>("/auth/sessions/revoke-others/"); toast.success(t("profile.othersSignedOut", { count: data.ended })); }
      void q.refetch();
    } catch (err) { toast.error(errorMessage(err, t("errors.generic"), t("errors.network"))); }
    finally { setBusy(false); }
  };
  return (
    <Panel title={t("profile.sessionsTitle")}>
      {q.isLoading ? <SkeletonRows rows={2} /> : q.isError ? <ErrorState message={t("errors.generic")} onRetry={() => void q.refetch()} /> : (
        <>
          <ul className="divide-y divide-line">{q.data?.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center gap-3 py-3">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{s.user_agent || "—"}</div>
                <div className="text-sm text-ink-soft">{s.ip_address} · {t("profile.lastActive")}: {formatDateTime(s.last_used_at)}</div>
              </div>
              {s.current ? <Badge tone="marking">{t("profile.thisDevice")}</Badge>
                : <Button variant="ghost" disabled={busy} onClick={() => void revoke(s.id)}>{t("profile.signOutDevice")}</Button>}
            </li>))}</ul>
          {(q.data?.length ?? 0) > 1 && <Button variant="secondary" busy={busy} className="mt-3" onClick={() => void revoke()}>{t("profile.signOutOthers")}</Button>}
        </>
      )}
    </Panel>
  );
}
