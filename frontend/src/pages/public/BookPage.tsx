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
    <div className="min-h-screen bg-[#fcfcfb]" style={themeScope}>
      <PublicNavbar />
      <main className="pb-12 pt-6 md:pt-8">
        <div className="container-page">
          <section className="relative mb-8 overflow-hidden rounded-[24px] border border-[#ebe5da] bg-[#faf8f5]">
            <div className="flex flex-col md:flex-row">
              <div className="flex flex-1 flex-col justify-center px-6 py-8 md:px-10 lg:px-12 lg:py-10">
                <span className="text-[11px] font-extrabold uppercase tracking-[0.2em] text-[#a17800]">Book a service</span>
                <h1 className="mt-3 font-display text-3xl font-black tracking-tight text-neutral-900 md:text-4xl lg:text-[40px] lg:leading-[1.1]">
                  Professional car care <br className="hidden md:block" />
                  at your doorstep.
                </h1>
                <p className="mt-3 text-[15px] font-semibold text-neutral-700">Quick. Easy. Reliable.</p>
                <p className="mt-1.5 max-w-sm text-[13px] leading-relaxed text-neutral-500">
                  No account needed — just your name and WhatsApp number at the end. Already with us? Log in and it's even faster.
                </p>
              </div>
              <div className="relative hidden w-full md:block md:w-[45%] lg:w-[50%]">
                <div className="absolute inset-0 bg-gradient-to-r from-[#faf8f5] to-transparent z-10" />
                <img src="/hero-img.png" alt="Blussit car care" className="absolute inset-0 h-full w-full object-cover object-[72%_center]" />
              </div>
            </div>
          </section>
          
          <PublicBookingWizard preselect={preselect} />
        </div>
      </main>
      <PublicFooter />
    </div>
  );
}
