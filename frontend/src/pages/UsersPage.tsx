import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useQueryClient } from "@tanstack/react-query";
import { api, errorMessage, fieldErrors } from "../services/api";
import { useApiQuery, type Page } from "../services/hooks";
import { activeMembership, hasPermission, useAuth } from "../store/auth";
import { Badge, Button, EmptyState, ErrorState, Field, PageHeader, Select, SkeletonRows, TextInput, formatDateTime } from "../components/ui";
import { Dialog } from "../components/Dialog";
import { toast } from "../components/Toast";

interface Member {
  id: number; is_active: boolean; created_at: string;
  user: { id: number; email: string; full_name: string; last_login: string | null; is_locked: boolean };
  role: { id: number; slug: string; name: string };
}
interface Role { id: number; slug: string; name: string; is_system: boolean; permission_codes: string[] }

export function UsersPage() {
  const { t } = useTranslation();
  const auth = useAuth();
  const membership = activeMembership(auth);
  const canManage = hasPermission(auth, "users.manage");
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [showInactive, setShowInactive] = useState(false);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [confirm, setConfirm] = useState<Member | null>(null);
  const [roleChange, setRoleChange] = useState<{ member: Member; role: Role } | null>(null);
  const users = useApiQuery<Page<Member>>("/users/", { search: search || undefined, include_inactive: showInactive ? 1 : undefined });
  const roles = useApiQuery<Page<Role>>("/roles/", undefined, { enabled: canManage });
  const network = t("errors.network");
  const refresh = () => qc.invalidateQueries({ queryKey: [auth.activeOrgId, "/users/"] });

  const requestRoleChange = (m: Member, roleId: number) => {
    const role = roles.data?.results.find((r) => r.id === roleId);
    if (role && role.id !== m.role.id) setRoleChange({ member: m, role });
  };
  const changeRole = async () => {
    if (!roleChange) return;
    try { await api.patch(`/users/${roleChange.member.id}/`, { role_id: roleChange.role.id }); toast.success(t("users.roleChanged")); void refresh(); }
    catch (err) { toast.error(errorMessage(err, t("errors.generic"), network)); }
    setRoleChange(null);
  };
  const roleControl = (m: Member) => canManage && m.is_active && roles.data ? (
    <label className="block"><span className="sr-only">{t("users.changeRole")}</span>
      <Select value={m.role.id} onChange={(e) => requestRoleChange(m, Number(e.target.value))}>
        {roles.data.results.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
      </Select></label>) : <span>{m.role.name}</span>;
  const statusBadges = (m: Member) => (<span className="space-x-1">
    {m.is_active ? <Badge tone="good">{t("common.active")}</Badge> : <Badge>{t("common.inactive")}</Badge>}
    {m.user.is_locked && <Badge tone="bad">{t("users.locked")}</Badge>}</span>);
  const accessAction = (m: Member) => canManage && (m.is_active
    ? <Button variant="ghost" onClick={() => setConfirm(m)}>{t("users.deactivate")}</Button>
    : <Button variant="ghost" onClick={() => void setActive(m, true)}>{t("users.reactivate")}</Button>);
  const setActive = async (m: Member, active: boolean) => {
    try {
      if (active) await api.post(`/users/${m.id}/reactivate/`); else await api.delete(`/users/${m.id}/`);
      toast.success(active ? t("users.reactivated") : t("users.deactivated"));
      setConfirm(null); void refresh();
    } catch (err) { toast.error(errorMessage(err, t("errors.generic"), network)); setConfirm(null); }
  };

  return (
    <>
      <PageHeader title={t("users.title")} lede={t("users.lede", { org: membership?.organization.name ?? "" })}
        actions={canManage && <Button onClick={() => setInviteOpen(true)}>{t("users.invite")}</Button>} />
      <div className="mb-4 flex flex-wrap items-center gap-4">
        <label className="w-72"><span className="sr-only">{t("common.search")}</span>
          <TextInput id="user-search" placeholder={t("common.search")} value={search} onChange={(e) => setSearch(e.target.value)} />
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />{t("users.showInactive")}
        </label>
      </div>
      {users.isLoading ? <SkeletonRows /> : users.isError ? (
        <ErrorState message={errorMessage(users.error, t("errors.generic"), network)} onRetry={() => void users.refetch()} />
      ) : !users.data?.results.length ? <EmptyState message={t("users.empty")} /> : (<>
        <ul className="space-y-3 md:hidden">{users.data.results.map((m) => (
          <li key={m.id} className="space-y-2 rounded-md border border-line bg-panel p-4">
            <div><div className="font-medium">{m.user.full_name || m.user.email}</div><div className="break-all text-sm text-ink-soft">{m.user.email}</div></div>
            {roleControl(m)}
            <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
              {statusBadges(m)}
              <span className="text-ink-soft">{t("users.lastSignIn")}: {formatDateTime(m.user.last_login, membership?.organization.timezone, t("common.never"))}</span>
            </div>
            {accessAction(m)}
          </li>))}
        </ul>
        <div className="hidden overflow-x-auto rounded-md border border-line bg-panel md:block">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-line text-ink-soft"><tr>
              <th className="px-4 py-3">{t("common.name")}</th><th className="px-4 py-3">{t("common.role")}</th>
              <th className="px-4 py-3">{t("users.lastSignIn")}</th><th className="px-4 py-3">{t("common.status")}</th>
              {canManage && <th className="px-4 py-3"><span className="sr-only">Actions</span></th>}
            </tr></thead>
            <tbody>{users.data.results.map((m) => (
              <tr key={m.id} className="border-b border-line last:border-0">
                <td className="px-4 py-3"><div className="font-medium">{m.user.full_name || m.user.email}</div><div className="text-ink-soft">{m.user.email}</div></td>
                <td className="px-4 py-3">{roleControl(m)}</td>
                <td className="px-4 py-3">{formatDateTime(m.user.last_login, membership?.organization.timezone, t("common.never"))}</td>
                <td className="px-4 py-3">{statusBadges(m)}</td>
                {canManage && <td className="px-4 py-3 text-right">{accessAction(m)}</td>}
              </tr>))}</tbody>
          </table>
        </div>
      </>)}
      {inviteOpen && <InviteDialog roles={roles.data?.results ?? []} onClose={() => setInviteOpen(false)} onDone={() => { setInviteOpen(false); void refresh(); }} />}
      <Dialog open={!!roleChange} title={t("users.changeRole")} onClose={() => setRoleChange(null)}>
        <p>{t("users.roleConfirm", { email: roleChange?.member.user.email, from: roleChange?.member.role.name, to: roleChange?.role.name })}</p>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setRoleChange(null)}>{t("common.cancel")}</Button>
          <Button onClick={() => void changeRole()}>{t("users.changeRole")}</Button>
        </div>
      </Dialog>
      <Dialog open={!!confirm} title={t("users.deactivate")} onClose={() => setConfirm(null)}>
        <p>{t("users.deactivateConfirm", { email: confirm?.user.email })}</p>
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setConfirm(null)}>{t("common.cancel")}</Button>
          <Button variant="danger" onClick={() => confirm && void setActive(confirm, false)}>{t("users.deactivate")}</Button>
        </div>
      </Dialog>
    </>
  );
}

function InviteDialog({ roles, onClose, onDone }: { roles: Role[]; onClose: () => void; onDone: () => void }) {
  const { t } = useTranslation();
  const [serverError, setServerError] = useState<string | null>(null);
  const schema = z.object({
    email: z.string().trim().email(t("auth.invalidEmail")),
    full_name: z.string().trim().max(150),
    role_id: z.coerce.number().int().positive(t("common.required")),
  });
  const viewer = roles.find((r) => r.slug === "viewer")?.id;
  const { register, handleSubmit, setError, formState: { errors, isSubmitting } } = useForm<z.input<typeof schema>, undefined, z.output<typeof schema>>({
    resolver: zodResolver(schema), defaultValues: { email: "", full_name: "", role_id: viewer ?? 0 } });
  const onSubmit = handleSubmit(async (values) => {
    setServerError(null);
    try { await api.post("/users/", values); toast.success(t("users.added")); onDone(); }
    catch (err) {
      const fields = fieldErrors(err);
      if (fields.email) setError("email", { message: fields.email });
      else setServerError(errorMessage(err, t("errors.generic"), t("errors.network")));
    }
  });
  return (
    <Dialog open title={t("users.inviteTitle")} onClose={onClose}>
      <p className="mb-4 text-ink-soft">{t("users.inviteLede")}</p>
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        {serverError && <ErrorState message={serverError} />}
        <Field id="invite-email" label={t("common.email")} error={errors.email?.message}>
          <TextInput id="invite-email" type="email" placeholder={t("auth.emailPlaceholder")} invalid={!!errors.email} {...register("email")} />
        </Field>
        <Field id="invite-name" label={t("users.fullName")} error={errors.full_name?.message}>
          <TextInput id="invite-name" placeholder="Asha Mwakyusa" {...register("full_name")} />
        </Field>
        <Field id="invite-role" label={t("common.role")} error={errors.role_id?.message}>
          <Select id="invite-role" {...register("role_id")}>{roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</Select>
        </Field>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose}>{t("common.cancel")}</Button>
          <Button type="submit" busy={isSubmitting}>{t("users.invite")}</Button>
        </div>
      </form>
    </Dialog>
  );
}
