import { type CSSProperties } from "react";
import { useNavigate } from "react-router-dom";
import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { LandingHero } from "../../components/public/LandingHeroNew";
import { ServicesShowcase } from "../../components/public/landing/ServicesShowcase";
import { HowItWorksStrip } from "../../components/public/landing/HowItWorksStrip";
import { PlansShowcase } from "../../components/public/landing/PlansShowcase";
import { VideoReviews } from "../../components/public/landing/VideoReviews";
import { ReviewsShowcase } from "../../components/public/landing/ReviewsShowcase";
import { FaqSection } from "../../components/public/landing/FaqSection";
import { LaunchOfferPopup, usePromotedOffer } from "../../components/public/LaunchOfferPopup";
import { PageSeo } from "../../components/shared/PageSeo";
import { useAuth } from "../../context/AuthContext";

/**
 * Landing page, kept deliberately short: hero → services → how it works →
 * plans → video reviews → written reviews. The older sections (offers marquee, Why Blussit, premium
 * banner) still live in LandingSections.tsx if they are ever needed again.
 */
export default function LandingPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  // A logged-in customer already has an account, saved vehicles/addresses —
  // send them straight to the after-login booking page instead of the
  // guest wizard (which exists only to let a not-yet-logged-in visitor
  // book at all). Staff roles never see this landing page's CTA in
  // practice, but the guest wizard remains a safe fallback for them too.
  const bookPath = user?.role === "customer" ? "/app/book" : "/book";
  const bookService = (slug?: string) => navigate(slug ? `${bookPath}?service=${encodeURIComponent(slug)}` : bookPath);
  const offer = usePromotedOffer();
  const claimOffer = () => offer && bookService(offer.service.slug);

  const themeScope = {
    "--color-primary": "#000000",
    "--color-primary-dark": "#000000",
    "--color-secondary": "#FACC15",
    "--color-accent": "#FACC15",
  } as CSSProperties;

  return (
    <div className="min-h-screen bg-white text-black selection:bg-black selection:text-white" style={themeScope}>
      <PageSeo path="/" />
      <PublicNavbar />
      {offer && <LaunchOfferPopup offer={offer} onClaim={claimOffer} />}
      <LandingHero onBook={bookService} />
      <ServicesShowcase limit={6} />
      <HowItWorksStrip />
      <PlansShowcase />
      <VideoReviews />
      <ReviewsShowcase />
      <FaqSection />
      <PublicFooter />
    </div>
  );
}
