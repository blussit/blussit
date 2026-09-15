import { type CSSProperties } from "react";
import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { PageSeo } from "../../components/shared/PageSeo";
import { QuickBookFlow } from "../../components/booking/QuickBookFlow";
import { useAuth } from "../../context/AuthContext";

/**
 * /book — the public booking page. No login wall: the two-step quick flow
 * books straight from a name and phone number. A signed-in customer who
 * lands here gets the same flow with their details prefilled.
 */
export default function BookPage() {
  const { user } = useAuth();
  const themeScope = {
    "--color-primary": "#000000",
    "--color-primary-dark": "#000000",
    "--color-secondary": "#FACC15",
    "--color-accent": "#FACC15",
  } as CSSProperties;

  return (
    <div className="min-h-screen bg-white" style={themeScope}>
      <PageSeo path="/book" />
      <PublicNavbar />
      <div className="bg-white pb-12 pt-8 sm:pt-12">
        <div className="container-page">
          <QuickBookFlow mode={user?.role === "customer" ? "customer" : "public"} />
        </div>
      </div>
      <PublicFooter />
    </div>
  );
}
