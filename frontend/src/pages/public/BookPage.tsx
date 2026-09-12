import { type CSSProperties, useEffect, useState } from "react";
import { Navigate, useSearchParams } from "react-router-dom";
import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { PublicBookingWizard, type WizardPreselect } from "../../components/public/PublicBookingWizard";
import { useAuth } from "../../context/AuthContext";

export default function BookPage() {
  const [searchParams] = useSearchParams();
  const { user } = useAuth();
  const [preselect, setPreselect] = useState<WizardPreselect | null>(null);

  // The guest wizard is the simple, first-time version. A customer who is
  // already signed in has saved cars, addresses, passes and the multi-car
  // flow waiting in the real booking page — every "Book now" on the
  // public site lands them there instead. The preselected service rides
  // along.
  if (user?.role === "customer") {
    const serviceId = searchParams.get("serviceId");
    return <Navigate to={serviceId ? `/app/book?service=${serviceId}` : "/app/book"} replace />;
  }

  useEffect(() => {
    const serviceId = searchParams.get("serviceId");
    if (serviceId) {
      setPreselect({ serviceId });
    }
  }, [searchParams]);

  const themeScope = {
    "--color-primary": "#000000",
    "--color-primary-dark": "#000000",
    "--color-secondary": "#FACC15",
    "--color-accent": "#FACC15",
  } as CSSProperties;

  return (
    <div className="min-h-screen bg-white" style={themeScope}>
      <PublicNavbar />
      {/* The wizard shell carries its own title/step rail — the page just
          gives it a white ground and breathing room, same as after login. */}
      <div className="bg-white pb-12 pt-8 sm:pt-12">
        <div className="container-page">
          <PublicBookingWizard preselect={preselect} />
        </div>
      </div>
      <PublicFooter />
    </div>
  );
}
