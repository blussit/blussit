import { type CSSProperties } from "react";
import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { useNavigate } from "react-router-dom";

import {
  HowItWorksSection,
  LandingHero,
  PremiumBanner,
  ServicesHorizontalScroll,
  VideoReviewsScroll,
  WhyBlussit,
  OffersCarousel,

} from "../../components/public/LandingSections";

export default function LandingPage() {
  const navigate = useNavigate();

  const themeScope = {
    "--color-primary": "#000000",
    "--color-primary-dark": "#000000",
    "--color-primary-light": "#1A1A1A",
    "--color-secondary": "#FACC15",
    "--color-accent": "#FACC15",
  } as CSSProperties;

  return (
    <div
      className="min-h-screen bg-white text-black selection:bg-black selection:text-white"
      style={themeScope}
    >
      <PublicNavbar />

      <LandingHero
        onBook={() => navigate("/book")}
      />

      <OffersCarousel
        onBook={() => navigate("/book")}
      />

      <ServicesHorizontalScroll
        onBook={(id) =>
          navigate(`/book?serviceId=${id || ""}`)
        }
      />

      <WhyBlussit />

      <HowItWorksSection />

      <PremiumBanner
        onBook={() => navigate("/book")}
      />

      <VideoReviewsScroll />

      <PublicFooter />
    </div>
  );
}