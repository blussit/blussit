import { CaptainSocietiesToday } from "../../components/captain/society/CaptainSocietiesToday";
import { TopBar } from "../../components/captain/ui";
import { useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";

/** Captain: today's societies (bucket-wash attendance). Also shown on Today when he has society duty. */
export default function CaptainSocietiesPage() {
  const { t, language } = useCaptainTranslation();
  return (
    <>
      <TopBar title={t("captain.v2.societies")} back="/captain" />
      <CaptainSocietiesToday language={language === "hi" ? "hi" : "en"} />
    </>
  );
}
