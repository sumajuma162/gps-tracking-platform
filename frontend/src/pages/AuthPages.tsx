import { useState, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { PublicLayout } from "../layouts/PublicLayout";
import { Button, ErrorState, Field, TextInput } from "../components/ui";
import { api, errorMessage, fieldErrors } from "../services/api";
import { login } from "../services/auth";
import { safeNext } from "../features/auth/RequireAuth";
import { setLanguage } from "../i18n";
import { useAuth } from "../store/auth";

function AuthCard({ title, lede, children }: { title: string; lede?: string; children: ReactNode }) {
  return (
    <PublicLayout nav={false}>
      <div className="mx-auto max-w-md px-5 py-14">
        <div className="rounded-lg border border-line bg-panel p-7">
          <h1 className="text-3xl font-bold">{title}</h1>
          {lede && <p className="mt-1 text-ink-soft">{lede}</p>}
          <div className="mt-6">{children}</div>
        </div>
      </div>
    </PublicLayout>
  );
}

export function LoginPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [serverError, setServerError] = useState<string | null>(null);
  const schema = z.object({
    email: z.string().trim().email(t("auth.invalidEmail")),
    password: z.string().min(1, t("auth.passwordRequired")),
  });
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<z.infer<typeof schema>>({ resolver: zodResolver(schema) });

  const onSubmit = handleSubmit(async ({ email, password }) => {
    setServerError(null);
    try {
      await login(email, password);
      const lang = useAuth.getState().user?.language;
      if (lang) setLanguage(lang);
      navigate(safeNext(params.get("next")), { replace: true });
    } catch (err) {
      setServerError(errorMessage(err, t("errors.generic"), t("errors.network")));
    }
  });

  return (
    <AuthCard title={t("auth.signInTitle")} lede={t("auth.signInLede")}>
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        {params.get("reset") === "1" && !serverError && <p role="status" className="rounded-md bg-moving/10 p-3 text-moving">{t("auth.resetDone")}</p>}
        {serverError && <ErrorState message={serverError} />}
        <Field id="email" label={t("common.email")} error={errors.email?.message}>
          <TextInput id="email" type="email" autoComplete="username" placeholder={t("auth.emailPlaceholder")} invalid={!!errors.email} {...register("email")} />
        </Field>
        <Field id="password" label={t("common.password")} error={errors.password?.message}>
          <TextInput id="password" type="password" autoComplete="current-password" placeholder={t("auth.passwordPlaceholder")} invalid={!!errors.password} {...register("password")} />
        </Field>
        <Button type="submit" busy={isSubmitting} className="w-full">{isSubmitting ? t("auth.signingIn") : t("common.signIn")}</Button>
        <p className="text-center text-sm"><Link to="/forgot-password" className="font-medium underline">{t("auth.forgot")}</Link></p>
      </form>
    </AuthCard>
  );
}

export function ForgotPasswordPage() {
  const { t } = useTranslation();
  const [done, setDone] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const schema = z.object({ email: z.string().trim().email(t("auth.invalidEmail")) });
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<z.infer<typeof schema>>({ resolver: zodResolver(schema) });
  const onSubmit = handleSubmit(async ({ email }) => {
    setServerError(null);
    try { await api.post("/auth/password/forgot/", { email }); setDone(true); }
    catch (err) { setServerError(errorMessage(err, t("errors.generic"), t("errors.network"))); }
  });
  return (
    <AuthCard title={t("auth.forgotTitle")} lede={done ? undefined : t("auth.forgotLede")}>
      {done ? <p role="status">{t("auth.sent")}</p> : (
        <form onSubmit={onSubmit} noValidate className="space-y-4">
          {serverError && <ErrorState message={serverError} />}
          <Field id="email" label={t("common.email")} error={errors.email?.message}>
            <TextInput id="email" type="email" autoComplete="email" placeholder={t("auth.emailPlaceholder")} invalid={!!errors.email} {...register("email")} />
          </Field>
          <Button type="submit" busy={isSubmitting} className="w-full">{t("auth.sendLink")}</Button>
        </form>
      )}
      <p className="mt-5 text-center text-sm"><Link to="/login" className="font-medium underline">{t("auth.backToSignIn")}</Link></p>
    </AuthCard>
  );
}

export function ResetPasswordPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [serverError, setServerError] = useState<string | null>(null);
  const invite = params.get("invite") === "1";
  const schema = z.object({
    new_password: z.string().min(12, t("auth.tooShort")),
    confirm: z.string(),
  }).refine((v) => v.new_password === v.confirm, { path: ["confirm"], message: t("auth.mismatch") });
  const { register, handleSubmit, setError, formState: { errors, isSubmitting } } = useForm<z.infer<typeof schema>>({ resolver: zodResolver(schema) });
  const onSubmit = handleSubmit(async ({ new_password }) => {
    setServerError(null);
    try {
      await api.post("/auth/password/reset/", { uid: params.get("uid") ?? "", token: params.get("token") ?? "", new_password });
      navigate("/login?reset=1", { replace: true });
    } catch (err) {
      const fields = fieldErrors(err);
      if (fields.new_password) setError("new_password", { message: fields.new_password });
      else setServerError(errorMessage(err, t("auth.linkInvalid"), t("errors.network")));
    }
  });
  return (
    <AuthCard title={invite ? t("auth.inviteTitle") : t("auth.resetTitle")}>
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        {serverError && <ErrorState message={serverError} />}
        <Field id="new_password" label={t("auth.newPassword")} error={errors.new_password?.message} hint={t("auth.passwordRules")}>
          <TextInput id="new_password" type="password" autoComplete="new-password" invalid={!!errors.new_password} {...register("new_password")} />
        </Field>
        <Field id="confirm" label={t("auth.confirmPassword")} error={errors.confirm?.message}>
          <TextInput id="confirm" type="password" autoComplete="new-password" invalid={!!errors.confirm} {...register("confirm")} />
        </Field>
        <Button type="submit" busy={isSubmitting} className="w-full">{t("auth.setPassword")}</Button>
      </form>
    </AuthCard>
  );
}
