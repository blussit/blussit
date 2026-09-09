import { type CSSProperties } from "react";
import { useNavigate } from "react-router-dom";
import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { LandingHero } from "../../components/public/LandingSections";
import { ServicesShowcase } from "../../components/public/landing/ServicesShowcase";
import { HowItWorksStrip } from "../../components/public/landing/HowItWorksStrip";
import { PlansShowcase } from "../../components/public/landing/PlansShowcase";
import { ReviewsShowcase } from "../../components/public/landing/ReviewsShowcase";

/**
 * Landing page, kept deliberately short: hero → services → how it works →
 * plans → reviews. The older sections (offers marquee, Why Blussit, premium
 * banner) still live in LandingSections.tsx if they are ever needed again.
 */
export default function LandingPage() {
  const navigate = useNavigate();

  const themeScope = {
    "--color-primary": "#000000",
    "--color-primary-dark": "#000000",
    "--color-secondary": "#FACC15",
    "--color-accent": "#FACC15",
  } as CSSProperties;

  return (
    <div className="min-h-screen bg-white text-black selection:bg-black selection:text-white" style={themeScope}>
      <PublicNavbar />
      <LandingHero onBook={(serviceId) => navigate(serviceId ? `/book?serviceId=${serviceId}` : "/book")} />
      <ServicesShowcase limit={6} />
      <HowItWorksStrip />
      <PlansShowcase />
      <ReviewsShowcase />
      <PublicFooter />
    </div>
  );
}
