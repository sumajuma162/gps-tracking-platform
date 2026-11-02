import { useTranslation } from "react-i18next";
import { Badge } from "../../components/ui";

const TONES: Record<string, "good" | "warn" | "bad" | "neutral"> = {
  active: "good", installed: "good", maintenance: "warn", suspended: "warn", in_stock: "neutral",
  inactive: "neutral", cancelled: "bad", faulty: "bad", returned: "neutral", retired: "neutral",
};

export function StatusBadge({ value }: { value: unknown }) {
  const { t } = useTranslation();
  const v = String(value ?? "");
  return <Badge tone={TONES[v] ?? "neutral"}>{t(`choice.${v}`, { defaultValue: v })}</Badge>;
}
