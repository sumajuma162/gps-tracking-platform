import type { ReactNode } from "react";

export type Row = { id: number } & Record<string, unknown>;

export interface OptionSource {
  /** API list path, e.g. "/vehicle-groups/". */
  path: string;
  label: (row: Row) => string;
  params?: Record<string, unknown>;
  /** Which field of a row is the submitted value (default "id"). */
  value?: (row: Row) => string | number;
}

export interface FieldDef {
  name: string;
  /** i18n key for the label. */
  label: string;
  type?: "text" | "number" | "date" | "select" | "multiselect" | "textarea" | "checkbox" | "password" | "email" | "tel" | "time";
  required?: boolean;
  maxLength?: number;
  min?: number;
  max?: number;
  step?: string;
  pattern?: { value: RegExp; message: string };
  placeholder?: string;
  /** i18n key for help text. */
  hint?: string;
  /** Static choices: value -> i18n key. */
  choices?: { value: string; label: string }[];
  options?: OptionSource;
  /** Only editable when creating (e.g. a tracker's IMEI). */
  createOnly?: boolean;
  /** Hide on edit (e.g. write-only secrets are shown empty). */
  writeOnlyBlankOnEdit?: boolean;
  /** Initial value when creating (e.g. a checkbox that starts ticked). */
  defaultValue?: string | number | boolean;
  /** A blank value is sent as null (the field is nullable in the API). Otherwise blanks are omitted so the
   *  server's default applies. */
  allowNull?: boolean;
}

export interface ColumnDef {
  key: string;
  /** i18n key */
  header: string;
  render: (row: Row) => ReactNode;
  /** The column used as the card title on phones. */
  primary?: boolean;
  /** Keep the cell on one line (registrations, IMEIs). */
  nowrap?: boolean;
}

export interface FilterDef {
  param: string;
  label: string;
  choices: { value: string; label: string }[];
}

export interface ResourceConfig {
  key: string;
  path: string;
  title: string;
  lede: string;
  /** Permission needed to see the page; "platform" means Super Administrators only. */
  viewPermission: string | "platform";
  managePermission: string | "platform";
  columns: ColumnDef[];
  fields: FieldDef[];
  filters?: FilterDef[];
  /** i18n key of the "empty" message. */
  empty: string;
  /** Text used in the delete confirmation. */
  describe: (row: Row) => string;
  canDelete?: boolean;
  /** Map an API row to flat form values (e.g. nested params -> fields), and form payload back to the API shape. */
  toForm?: (row: Row) => Row;
  fromForm?: (payload: Record<string, unknown>) => Record<string, unknown>;
  /** Show the search box (only for endpoints that support ?search=). Default true. */
  searchable?: boolean;
  canCreate?: boolean;
  rowActions?: (row: Row, helpers: RowHelpers) => ReactNode;
  headerActions?: (helpers: RowHelpers) => ReactNode;
}

export interface RowHelpers {
  refresh: () => void;
  canManage: boolean;
}
