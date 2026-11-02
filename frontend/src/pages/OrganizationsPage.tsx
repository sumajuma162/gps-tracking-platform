import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useQueryClient } from "@tanstack/react-query";
import { api, errorMessage, fieldErrors } from "../services/api";
import { useApiQuery, type Page } from "../services/hooks";
import { useAuth } from "../store/auth";
import { Badge, Button, EmptyState, ErrorState, Field, PageHeader, SkeletonRows, TextInput, formatDateTime } from "../components/ui";
import { Dialog } from "../components/Dialog";
import { toast } from "../components/Toast";

interface Org { id: number; name: string; slug: string; timezone: string; is_active: boolean; created_at: string }

export function OrganizationsPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const isPlatform = useAuth((s) => !!s.user?.is_platform_admin);
  const orgs = useApiQuery<Page<Org>>("/organizations/");
  const [createOpen, setCreateOpen] = useState(false);
  const [deactivate, setDeactivate] = useState<Org | null>(null);
  const [support, setSupport] = useState<Org | null>(null);
  const refresh = () => qc.invalidateQueries({ predicate: (q) => q.queryKey[1] === "/organizations/" });
  if (!isPlatform) return <ErrorState message={t("errors.forbidden")} />;

  const doDeactivate = async (o: Org) => {
    try { await api.delete(`/organizations/${o.id}/`); toast.success(t("orgs.deactivated")); void refresh(); }
    catch (err) { toast.error(errorMessage(err, t("errors.generic"), t("errors.network"))); }
    setDeactivate(null);
  };

  return (
    <>
      <PageHeader title={t("orgs.title")} lede={t("orgs.lede")} actions={<Button onClick={() => setCreateOpen(true)}>{t("orgs.create")}</Button>} />
      {orgs.isLoading ? <SkeletonRows /> : orgs.isError ? (
        <ErrorState message={errorMessage(orgs.error, t("errors.generic"), t("errors.network"))} onRetry={() => void orgs.refetch()} />
      ) : !orgs.data?.results.length ? <EmptyState message={t("orgs.lede")} /> : (
        <div className="overflow-x-auto rounded-md border border-line bg-panel">
          <table className="w-full min-w-[40rem] text-left text-sm">
            <thead className="border-b border-line text-ink-soft"><tr>
              <th className="px-4 py-3">{t("common.name")}</th><th className="px-4 py-3">{t("orgs.timezone")}</th>
              <th className="px-4 py-3">{t("common.status")}</th><th className="px-4 py-3"><span className="sr-only">Actions</span></th>
            </tr></thead>
            <tbody>{orgs.data.results.map((o) => (
              <tr key={o.id} className="border-b border-line last:border-0">
                <td className="px-4 py-3"><div className="font-medium">{o.name}</div><div className="text-ink-soft">{formatDateTime(o.created_at)}</div></td>
                <td className="px-4 py-3">{o.timezone}</td>
                <td className="px-4 py-3">{o.is_active ? <Badge tone="good">{t("common.active")}</Badge> : <Badge>{t("common.inactive")}</Badge>}</td>
                <td className="space-x-1 px-4 py-3 text-right">
                  <Button variant="ghost" onClick={() => setSupport(o)}>{t("orgs.supportAccess")}</Button>
                  {o.is_active && <Button variant="ghost" onClick={() => setDeactivate(o)}>{t("orgs.deactivate")}</Button>}
                </td>
              </tr>))}</tbody>
          </table>
        </div>
      )}
      {createOpen && <CreateOrgDialog onClose={() => setCreateOpen(false)} onDone={() => { setCreateOpen(false); void refresh(); }} />}
      {support && <SupportAccessDialog org={support} onClose={() => setSupport(null)} />}
      <Dialog open={!!deactivate} title={t("orgs.deactivate")} onClose={() => setDeactivate(null)}>
        <p>{t("orgs.deactivateConfirm", { name: deactivate?.name })}</p>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setDeactivate(null)}>{t("common.cancel")}</Button>
          <Button variant="danger" onClick={() => deactivate && void doDeactivate(deactivate)}>{t("orgs.deactivate")}</Button>
        </div>
      </Dialog>
    </>
  );
}

function CreateOrgDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { t } = useTranslation();
  const [serverError, setServerError] = useState<string | null>(null);
  const schema = z.object({
    name: z.string().trim().min(2, t("common.required")),
    timezone: z.string().trim().min(3, t("common.required")),
    admin_email: z.union([z.literal(""), z.string().trim().email(t("auth.invalidEmail"))]),
  });
  const { register, handleSubmit, setError, formState: { errors, isSubmitting } } = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema), defaultValues: { name: "", timezone: "Africa/Dar_es_Salaam", admin_email: "" } });
  const onSubmit = handleSubmit(async ({ admin_email, ...rest }) => {
    setServerError(null);
    try { await api.post("/organizations/", admin_email ? { ...rest, admin_email } : rest); toast.success(t("orgs.created")); onDone(); }
    catch (err) {
      const f = fieldErrors(err);
      if (f.timezone) setError("timezone", { message: f.timezone });
      else setServerError(errorMessage(err, t("errors.generic"), t("errors.network")));
    }
  });
  return (
    <Dialog open title={t("orgs.createTitle")} onClose={onClose}>
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        {serverError && <ErrorState message={serverError} />}
        <Field id="org-name" label={t("common.name")} error={errors.name?.message}>
          <TextInput id="org-name" placeholder="Demo Logistics Ltd" invalid={!!errors.name} {...register("name")} />
        </Field>
        <Field id="org-tz" label={t("orgs.timezone")} error={errors.timezone?.message}>
          <TextInput id="org-tz" placeholder="Africa/Dar_es_Salaam" invalid={!!errors.timezone} {...register("timezone")} />
        </Field>
        <Field id="org-admin" label={t("orgs.adminEmail")} error={errors.admin_email?.message}>
          <TextInput id="org-admin" type="email" placeholder={t("auth.emailPlaceholder")} invalid={!!errors.admin_email} {...register("admin_email")} />
        </Field>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" busy={isSubmitting}>{t("orgs.create")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

function SupportAccessDialog({ org, onClose }: { org: Org; onClose: () => void }) {
  const { t } = useTranslation();
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (reason.trim().length < 10) { setError(t("orgs.reasonShort")); return; }
    setBusy(true);
    try { await api.post(`/organizations/${org.id}/break-glass/`, { reason }); toast.success(t("orgs.granted")); onClose(); }
    catch (err) { setError(errorMessage(err, t("errors.generic"), t("errors.network"))); }
    finally { setBusy(false); }
  };
  return (
    <Dialog open title={`${t("orgs.supportAccess")}: ${org.name}`} onClose={onClose}>
      <Field id="bg-reason" label={t("orgs.reason")} error={error ?? undefined}>
        <TextInput id="bg-reason" value={reason} invalid={!!error} onChange={(e) => setReason(e.target.value)} placeholder="Ticket #4521: tracker offline" />
      </Field>
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="secondary" onClick={onClose}>{t("common.cancel")}</Button>
        <Button busy={busy} onClick={() => void submit()}>{t("orgs.grant")}</Button>
      </div>
    </Dialog>
  );
}
