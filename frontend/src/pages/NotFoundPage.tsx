import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

export function NotFoundPage() {
  const { t } = useTranslation();
  return (
    <div className="mx-auto max-w-md px-5 py-20 text-center">
      <h1 className="text-3xl font-bold">404</h1>
      <p className="mt-2 text-ink-soft">{t("errors.notFound")}</p>
      <Link to="/" className="mt-6 inline-block font-semibold underline">{t("landing.footer")}</Link>
    </div>
  );
}
