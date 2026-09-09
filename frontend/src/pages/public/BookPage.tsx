import { type CSSProperties, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { PublicBookingWizard, type WizardPreselect } from "../../components/public/PublicBookingWizard";

export default function BookPage() {
  const [searchParams] = useSearchParams();
  const [preselect, setPreselect] = useState<WizardPreselect | null>(null);

  useEffect(() => {
    const serviceId = searchParams.get("serviceId");
    if (serviceId) {
      setPreselect({ serviceId });
    }
  }, [searchParams]);

  const themeScope = {
    "--color-primary": "#000000",
    "--color-primary-dark": "#000000",
    "--color-secondary": "#FACC15",
    "--color-accent": "#FACC15",
  } as CSSProperties;

  return (
    <div className="min-h-screen bg-white" style={themeScope}>
      <PublicNavbar />
      <div className="bg-cream pb-10 pt-6 sm:pb-12 sm:pt-12">
        <div className="container-page">
          <div className="mx-auto mb-5 max-w-2xl text-center sm:mb-8">
            <h1 className="font-display text-[28px] font-black tracking-tight text-[#111] sm:text-4xl">Book a service</h1>
            <p className="mt-2 text-[14px] text-neutral-500 sm:mt-3 sm:text-lg">
              No account needed — just your name and WhatsApp number at the end.
            </p>
          </div>
          <PublicBookingWizard preselect={preselect} />
        </div>
      </div>
      <PublicFooter />
    </div>
  );
}
