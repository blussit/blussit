import { type CSSProperties } from "react";
import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { ServicesShowcase } from "../../components/public/landing/ServicesShowcase";

export default function ServicesPage() {
  const themeScope = {
    "--color-primary": "#000000",
    "--color-primary-dark": "#000000",
    "--color-secondary": "#FACC15",
    "--color-accent": "#FACC15",
  } as CSSProperties;

  return (
    <div className="min-h-screen bg-white" style={themeScope}>
      <PublicNavbar />
      <ServicesShowcase
        id="all-services"
        title="All services"
        subtitle="Every service, with what's included and the price you pay. We come to your doorstep."
      />
      <PublicFooter />
    </div>
  );
}
