import { type CSSProperties } from "react";
import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { PlansSplit } from "../../components/public/LandingSections";

export default function PlansPage() {
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
      <div className="pt-24 pb-12">
        <PlansSplit />
      </div>
      <PublicFooter />
    </div>
  );
}
