import { useEffect } from "react";
import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { PageSeo } from "../../components/shared/PageSeo";
import { QuickBookFlow } from "../../components/booking/QuickBookFlow";
import { useAuth } from "../../context/AuthContext";
import { trackViewContent } from "../../lib/metaPixel";

/** Holds the page's shape while a saved session is being checked. */
function BookSkeleton() {
  return (
    <div className="mx-auto w-full max-w-6xl space-y-4 px-4 py-8" aria-busy="true" aria-label="Loading the booking page">
      <div className="h-8 w-56 animate-pulse rounded-[10px] bg-[#EEF1F5]" />
      <div className="h-[120px] animate-pulse rounded-[18px] bg-[#EEF1F5]" />
      <div className="h-[220px] animate-pulse rounded-[18px] bg-[#EEF1F5]" />
    </div>
  );
}

/**
 * /book — the public booking page (approved v2 mockup). No login wall: a
 * guest books from a name and phone number and confirms with an OTP; a
 * signed-in customer gets the same page with their details, saved
 * addresses and plans already in.
 */
export default function BookPage() {
  const { user, isLoading } = useAuth();
  useEffect(() => trackViewContent("Booking page"), []);
  const mode = user?.role === "customer" ? "customer" : "public";
  return (
    <div className="min-h-screen bg-[#F6F8FC]">
      <PageSeo path="/book" />
      <PublicNavbar />
      <main className="w-full">
        {/* The flow restores the unfinished booking for whoever is signed
            in — mounting it before the saved session is checked restored
            the guest's (empty) draft and then overwrote the customer's. It
            remounts (key) if the person changes. */}
        {isLoading ? <BookSkeleton /> : <QuickBookFlow key={mode} mode={mode} layout="page" />}
      </main>
      <PublicFooter />
    </div>
  );
}
