import { PublicNavbar } from "../../components/layout/PublicNavbar";
import { PublicFooter } from "../../components/layout/PublicFooter";
import { PageSeo } from "../../components/shared/PageSeo";
import { QuickBookFlow } from "../../components/booking/QuickBookFlow";
import { useAuth } from "../../context/AuthContext";

/**
 * /book — the public booking page (approved v2 mockup). No login wall: a
 * guest books from a name and phone number and confirms with an OTP; a
 * signed-in customer gets the same page with their details, saved
 * addresses and plans already in.
 */
export default function BookPage() {
  const { user } = useAuth();
  return (
    <div className="min-h-screen bg-[#F6F8FC]">
      <PageSeo path="/book" />
      <PublicNavbar />
      <main className="w-full">
        <QuickBookFlow mode={user?.role === "customer" ? "customer" : "public"} layout="page" />
      </main>
      <PublicFooter />
    </div>
  );
}
