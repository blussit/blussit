import { useEffect } from "react";
import { SpeedInsights } from "@vercel/speed-insights/react";
import { Navigation } from "@/components/layouts/Navigation";
import { Footer } from "@/components/layouts/Footer";
import { Hero } from "@/components/sections/Hero";
import { WhyBlussit } from "@/components/sections/WhyBlussit";
import { LeadForm } from "@/components/sections/LeadForm";
import { track } from "@/services/analytics";

export default function App() {
  useEffect(() => {
    track("page_view");
  }, []);

  return (
    <div className="min-h-screen bg-cream">
      <Navigation />
      <main>
        <Hero />
        <WhyBlussit />
        <LeadForm />
      </main>
      <Footer />
      <SpeedInsights />
    </div>
  );
}
