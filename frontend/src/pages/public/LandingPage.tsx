import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { LandingHero } from "../../components/public/LandingHeroNew";
import { ServicesShowcase } from "../../components/public/landing/ServicesShowcase";
import { HowItWorksStrip } from "../../components/public/landing/HowItWorksStrip";
import { PlansShowcase } from "../../components/public/landing/PlansShowcase";
import { VideoReviews } from "../../components/public/landing/VideoReviews";
import { ReviewsShowcase } from "../../components/public/landing/ReviewsShowcase";
import { FaqSection } from "../../components/public/landing/FaqSection";
import { LaunchOfferPopup, useActiveOffers } from "../../components/public/LaunchOfferPopup";
import { OfferBar } from "../../components/public/landing/OfferBar";
import { PageSeo } from "../../components/shared/PageSeo";
import { useAuth } from "../../context/AuthContext";
import { scrollToSection } from "../../lib/sections";

/**
 * The home page: hero → services → how it works → plans → video reviews →
 * written reviews → FAQ. The navbar jumps to these sections
 * (lib/sections.ts); /services, /plans and the other search-landing pages
 * (pages/public/seo) cover the same ground in depth.
 */
export default function LandingPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user } = useAuth();

  // Arriving with /#services, /#plans… (navbar or footer from another page,
  // or an old /services link): jump there once the section has rendered,
  // then once more after the sections above it have finished loading.
  useEffect(() => {
    const id = decodeURIComponent(location.hash.slice(1));
    if (!id) return;
    let tries = 0;
    let settle = 0;
    const timer = window.setInterval(() => {
      if (scrollToSection(id, "auto")) {
        window.clearInterval(timer);
        settle = window.setTimeout(() => scrollToSection(id, "auto"), 700);
      } else if (++tries > 40) {
        window.clearInterval(timer);
      }
    }, 75);
    return () => {
      window.clearInterval(timer);
      window.clearTimeout(settle);
    };
  }, [location.hash]);
  // A logged-in customer already has an account, saved vehicles/addresses —
  // send them straight to the after-login booking page instead of the
  // guest wizard (which exists only to let a not-yet-logged-in visitor
  // book at all). Staff roles never see this landing page's CTA in
  // practice, but the guest wizard remains a safe fallback for them too.
  const bookPath = user?.role === "customer" ? "/app/book" : "/book";
  const bookService = (slug?: string) => navigate(slug ? `${bookPath}?service=${encodeURIComponent(slug)}` : bookPath);
  const offers = useActiveOffers();
  const offer = offers[0] ?? null;
  const claimOffer = () => offer && bookService(offer.service.slug);

  return (
    <div className="min-h-screen bg-white text-[#0E1A33]">
      <PageSeo path="/" />
      <PublicNavbar />
      {/* Not over a visitor headed for a section (/#how-it-works from the
          footer) — the popup locks scrolling, so they'd be stuck at the top. */}
      {offer && !location.hash && <LaunchOfferPopup offer={offer} onClaim={claimOffer} />}
      <div className="relative">
        {/* Desktop: the offer floats over the photo's sky. Phones/tablets: the
            hero floats it above the features row instead. */}
        <div className="absolute inset-x-0 top-0 z-30 hidden lg:block">
          <OfferBar offers={offers} onBook={bookService} />
        </div>
        <LandingHero onBook={bookService} offer={<OfferBar offers={offers} onBook={bookService} />} />
      </div>
      <ServicesShowcase row />
      <HowItWorksStrip />
      <PlansShowcase />
      <VideoReviews id="reviews" />
      <ReviewsShowcase />
      <FaqSection />
      <PublicFooter />
    </div>
  );
}
