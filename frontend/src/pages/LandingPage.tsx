import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { PublicLayout } from "../layouts/PublicLayout";
import { RouteIllustration } from "../components/RouteIllustration";
import { Badge } from "../components/ui";

const DEVICES = [
  { model: "Teltonika FMC920", protocol: "Codec 8 / 8 Extended (TCP, UDP)", supported: true },
  { model: "GT06-compatible trackers", protocol: "GT06 (TCP)", supported: false },
  { model: "Phones and HTTP trackers", protocol: "JSON over HTTP", supported: false },
];

export function LandingPage() {
  const { t } = useTranslation();
  const features = [1, 2, 3, 4].map((n) => ({ title: t(`landing.f${n}t`), body: t(`landing.f${n}`) }));
  const steps = [1, 2, 3].map((n) => ({ title: t(`landing.how${n}t`), body: t(`landing.how${n}`) }));
  return (
    <PublicLayout>
      <section className="mx-auto grid max-w-6xl items-center gap-10 px-5 py-14 lg:grid-cols-[1.05fr_1fr] lg:py-20">
        <div>
          <h1 className="max-w-xl text-4xl font-bold leading-[1.05] sm:text-5xl">{t("landing.title")}</h1>
          <p className="mt-5 max-w-lg text-lg text-ink-soft">{t("landing.lede")}</p>
          <Link to="/login" className="mt-8 inline-block rounded-md bg-ink px-6 py-3 font-semibold text-white hover:bg-ink-deep">
            {t("landing.cta")}
          </Link>
        </div>
        <RouteIllustration label={t("landing.illustration")} />
      </section>

      <section id="features" className="border-y border-line bg-panel">
        <div className="mx-auto max-w-6xl px-5 py-14">
          <h2 className="text-3xl font-bold">{t("landing.featuresTitle")}</h2>
          <dl className="mt-8 grid gap-x-12 gap-y-8 sm:grid-cols-2">
            {features.map((f) => (
              <div key={f.title} className="border-l-4 border-marking pl-4">
                <dt className="text-lg font-semibold">{f.title}</dt>
                <dd className="mt-1 max-w-prose text-ink-soft">{f.body}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <section id="devices" className="mx-auto max-w-6xl px-5 py-14">
        <h2 className="text-3xl font-bold">{t("landing.devicesTitle")}</h2>
        <p className="mt-2 max-w-prose text-ink-soft">{t("landing.devicesLede")}</p>
        <div className="mt-6 overflow-x-auto rounded-md border border-line bg-panel">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-line text-ink-soft">
              <tr><th className="px-4 py-3 font-semibold">{t("landing.devModel")}</th><th className="px-4 py-3 font-semibold">{t("landing.devProtocol")}</th><th className="px-4 py-3 font-semibold">{t("landing.devStatus")}</th></tr>
            </thead>
            <tbody>
              {DEVICES.map((d) => (
                <tr key={d.model} className="border-b border-line last:border-0">
                  <td className="px-4 py-3 font-medium">{d.model}</td>
                  <td className="px-4 py-3 text-ink-soft">{d.protocol}</td>
                  <td className="px-4 py-3">{d.supported ? <Badge tone="good">{t("landing.statusSupported")}</Badge> : <Badge>{t("landing.statusInDevelopment")}</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section id="how" className="bg-ink text-white">
        <div className="mx-auto max-w-6xl px-5 py-14">
          <h2 className="text-3xl font-bold">{t("landing.howTitle")}</h2>
          <ol className="mt-8 grid gap-8 md:grid-cols-3">
            {steps.map((s, i) => (
              <li key={s.title}>
                <span className="font-display text-4xl font-bold text-marking">{i + 1}</span>
                <h3 className="mt-2 text-lg font-semibold">{s.title}</h3>
                <p className="mt-1 text-white/75">{s.body}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="mx-auto grid max-w-6xl gap-10 px-5 py-14 md:grid-cols-2">
        <div>
          <h2 className="text-2xl font-bold">{t("landing.privacyTitle")}</h2>
          <p className="mt-2 max-w-prose text-ink-soft">{t("landing.privacy")}</p>
        </div>
        <div id="about">
          <h2 className="text-2xl font-bold">{t("landing.aboutTitle")}</h2>
          <p className="mt-2 max-w-prose text-ink-soft">{t("landing.about")}</p>
        </div>
      </section>
    </PublicLayout>
  );
}
