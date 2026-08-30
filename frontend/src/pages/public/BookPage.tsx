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
    "--color-primary-light": "#1A1A1A",
    "--color-secondary": "#FACC15",
    "--color-accent": "#FACC15",
  } as CSSProperties;

  return (
    <div className="min-h-screen bg-white" style={themeScope}>
      <PublicNavbar />
      <div className="pt-24 pb-12 bg-white">
        <div className="container-page py-10">
          <div className="mx-auto mb-10 max-w-2xl text-center">
            <h1 className="font-display text-4xl font-black text-[#111] tracking-tight">Book a service</h1>
            <p className="mt-4 text-lg text-neutral-500">
              No account needed — just your name and WhatsApp number at the end. Already with us? Log in and it's even faster.
            </p>
          </div>
          <PublicBookingWizard preselect={preselect} />
        </div>
      </div>
      <PublicFooter />
    </div>
  );
}
