import { type CSSProperties } from "react";
import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { PageSeo } from "../../components/shared/PageSeo";
import { PlansShowcase } from "../../components/public/landing/PlansShowcase";

export default function PlansPage() {
  const themeScope = {
    "--color-primary": "#000000",
    "--color-primary-dark": "#000000",
    "--color-secondary": "#FACC15",
    "--color-accent": "#FACC15",
  } as CSSProperties;

  return (
    <div className="min-h-screen bg-white" style={themeScope}>
      <PageSeo path="/plans" />
      <PublicNavbar />
      <PlansShowcase showEmpty />
      <PublicFooter />
    </div>
  );
}
