import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { Hero } from "../../components/shared/Hero";
import { FeatureStrip } from "../../components/shared/FeatureStrip";
import { WhyChooseUs } from "../../components/shared/WhyChooseUs";
import { PlansSection } from "../../components/shared/PlansSection";
import { TestimonialsSection, StatsSection } from "../../components/shared/TestimonialsAndStats";
import { AboutFaqContact } from "../../components/shared/AboutFaqContact";

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-white">
      <PublicNavbar />
      <Hero />
      <FeatureStrip />
      <WhyChooseUs />
      <PlansSection />
      <TestimonialsSection />
      <StatsSection />
      <AboutFaqContact />
      <PublicFooter />
    </div>
  );
}
