import { useState } from "react";
import { useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { api, errorMessage, fieldErrors } from "../../services/api";
import { useApiQuery, type Page } from "../../services/hooks";
import { Button, ErrorState, Field, Select, TextInput } from "../../components/ui";
import type { FieldDef, ResourceConfig, Row } from "./types";

type Values = Record<string, unknown>;

function initialValue(f: FieldDef, row: Row | null): unknown {
  const v = row?.[f.name];
  if (f.writeOnlyBlankOnEdit) return "";
  if (!row && f.defaultValue !== undefined) return f.type === "checkbox" ? Boolean(f.defaultValue) : String(f.defaultValue);
  if (f.type === "checkbox") return Boolean(v);
  if (f.type === "multiselect") return Array.isArray(v) ? v.map(String) : [];
  // A required choice list has no blank option; start on its first choice (matches the API defaults).
  if (!row && f.required && f.choices?.length) return f.choices[0].value;
  return v === null || v === undefined ? "" : String(v);
}

/** Convert form strings to API values: numbers, nulls for blank optional fields, id arrays. */
export function toPayload(fields: FieldDef[], values: Values, editing: boolean): Values {
  const out: Values = {};
  for (const f of fields) {
    if (editing && f.createOnly) continue;
    const v = values[f.name];
    if (f.writeOnlyBlankOnEdit && editing && v === "") continue;
    const blank = v === "" || v === undefined || v === null;
    const numeric = f.type === "number" || (!!f.options && f.type === "select");
    if (f.type === "checkbox") out[f.name] = Boolean(v);
    else if (f.type === "multiselect") {
      // ids are sent as numbers; other choices (e.g. role slugs) stay strings
      const values = (Array.isArray(v) ? v : v ? [v] : []).map(String).filter((x) => x !== "");
      out[f.name] = values.every((x) => /^\d+$/.test(x)) ? values.map(Number) : values;
    }
    else if ((numeric || f.type === "date") && blank) { if (f.allowNull) out[f.name] = null; }
    else if (numeric) out[f.name] = Number(v);
    else out[f.name] = typeof v === "string" ? v.trim() : v;
  }
  return out;
}

function OptionsSelect({ f, id, register, multiple }: { f: FieldDef; id: string; register: ReturnType<typeof useForm>["register"]; multiple?: boolean }) {
  const { t } = useTranslation();
  const q = useApiQuery<Page<Row>>(f.options!.path, { page_size: 200, ...(f.options!.params ?? {}) });
  const rows = q.data?.results ?? [];
  return (
    <Select id={id} multiple={multiple} disabled={q.isLoading} size={multiple ? Math.min(6, Math.max(3, rows.length)) : undefined}
      {...register(f.name, { required: f.required ? t("common.required") : false })}>
      {!multiple && <option value="">{f.required ? t("res.choose") : t("res.none")}</option>}
      {rows.map((r) => <option key={r.id} value={String(f.options!.value ? f.options!.value(r) : r.id)}>{f.options!.label(r)}</option>)}
    </Select>
  );
}

export function ResourceForm({ config, row, onDone, onCancel }: { config: ResourceConfig; row: Row | null; onDone: () => void; onCancel: () => void }) {
  const { t } = useTranslation();
  const editing = row !== null;
  const formRow = row && config.toForm ? config.toForm(row) : row;
  const defaults = Object.fromEntries(config.fields.map((f) => [f.name, initialValue(f, formRow)]));
  const { register, handleSubmit, setError, formState: { errors, isSubmitting } } = useForm<Values>({ defaultValues: defaults });
  const [serverError, setServerError] = useState<string | null>(null);

  const onSubmit = handleSubmit(async (values) => {
    setServerError(null);
    const flat = toPayload(config.fields, values, editing);
    const payload = config.fromForm ? config.fromForm(flat) : flat;
    try {
      if (editing) await api.patch(`${config.path}${row.id}/`, payload);
      else await api.post(config.path, payload);
      onDone();
    } catch (err) {
      const fields = fieldErrors(err);
      const known = Object.keys(fields).filter((k) => config.fields.some((f) => f.name === k));
      known.forEach((k) => setError(k, { message: fields[k] }));
      const other = Object.entries(fields).filter(([k]) => !known.includes(k)).map(([, v]) => v).join(" ");
      if (!known.length || other) setServerError(other || errorMessage(err, t("errors.generic"), t("errors.network")));
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      {serverError && <ErrorState message={serverError} />}
      <div className="grid gap-4 sm:grid-cols-2">
        {config.fields.map((f) => {
          const id = `${config.key}-${f.name}`;
          const error = errors[f.name]?.message as string | undefined;
          const disabled = editing && f.createOnly;
          const rules = {
            required: f.required && !disabled ? t("common.required") : false,
            maxLength: f.maxLength ? { value: f.maxLength, message: t("res.tooLong", { max: f.maxLength }) } : undefined,
            min: f.min !== undefined ? { value: f.min, message: t("res.min", { min: f.min }) } : undefined,
            max: f.max !== undefined ? { value: f.max, message: t("res.max", { max: f.max }) } : undefined,
            pattern: f.pattern,
          };
          const wide = f.type === "textarea" || f.type === "multiselect";
          if (f.type === "checkbox") {
            return (
              <label key={f.name} className="flex items-center gap-2 text-sm font-semibold sm:col-span-2">
                <input type="checkbox" id={id} {...register(f.name)} /> {t(f.label)}
              </label>
            );
          }
          let input;
          if (f.type === "textarea") {
            input = <textarea id={id} rows={4} placeholder={f.placeholder} aria-invalid={!!error || undefined}
              className="w-full rounded-md border border-line bg-panel px-3 py-2 outline-none focus:border-ink" {...register(f.name, rules)} />;
          } else if (f.options) {
            input = <OptionsSelect f={f} id={id} register={register as never} multiple={f.type === "multiselect"} />;
          } else if (f.choices) {
            const multiple = f.type === "multiselect";       // static choices can be multi-select too
            input = (
              <Select id={id} multiple={multiple} size={multiple ? Math.min(6, Math.max(3, f.choices.length)) : undefined}
                {...register(f.name, rules)}>
                {!f.required && !multiple && <option value="">{t("res.none")}</option>}
                {f.choices.map((c) => <option key={c.value} value={c.value}>{t(c.label)}</option>)}
              </Select>
            );
          } else {
            input = <TextInput id={id} type={f.type ?? "text"} step={f.step} placeholder={f.placeholder} disabled={disabled}
              autoComplete={f.type === "password" ? "new-password" : "off"} invalid={!!error} {...register(f.name, rules)} />;
          }
          return (
            <div key={f.name} className={wide ? "sm:col-span-2" : ""}>
              <Field id={id} label={`${t(f.label)}${f.required ? " *" : ""}`} error={error} hint={f.hint ? t(f.hint) : undefined}>{input}</Field>
            </div>
          );
        })}
      </div>
      <div className="flex justify-end gap-2 border-t border-line pt-4">
        <Button type="button" variant="secondary" onClick={onCancel}>{t("common.cancel")}</Button>
        <Button type="submit" busy={isSubmitting}>{editing ? t("common.save") : t("res.add")}</Button>
      </div>
    </form>
  );
}
