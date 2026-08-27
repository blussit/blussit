import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { CheckCircle2, Gift, Home, LayoutDashboard, LogIn, ReceiptText } from "lucide-react";
import { purchaseConfirmationApi } from "../../api/purchaseConfirmation";
import { Button, Card, CardBody, PageLoader, Spinner } from "../../components/ui";
import { useAuth } from "../../context/AuthContext";
import { format } from "../../lib/date";

const ROLE_HOME: Record<string, string> = { customer: "/app", manager: "/manager", admin: "/admin", captain: "/captain" };

/**
 * A fixed, PUBLIC confirmation page (deliberately outside the login-gated
 * portals — see App.tsx) a customer lands on right after completing a
 * purchase — a booking OR a subscription — separate from either one's own
 * (per-ID) detail page specifically so ad platforms (Meta/Google Ads) have
 * one stable URL to point a "purchase completed" conversion event at.
 *
 * SECURED against being reached by typing a URL: the only thing this page
 * reads from the query string is an opaque, single-purpose, short-lived
 * token (?token=...) minted server-side the instant a real booking/
 * subscription succeeds (see PurchaseConfirmationModel) — never a raw
 * booking/subscription id. No valid token can be guessed or constructed
 * by hand, and an invalid/missing/expired one redirects straight to the
 * public homepage instead of ever rendering the confirmation UI.
 *
 * Public rather than customer-only because a not-yet-logged-in guest
 * (once guest checkout exists) needs to land here too, right after an
 * account gets auto-created for them — they have no session yet, so a
 * "Return to dashboard" button would be meaningless. The page checks auth
 * state itself and shows the right actions either way.
 */
export default function ThankYouPage() {
  const navigate = useNavigate();
  const { user, isLoading: authLoading } = useAuth();
  const [params] = useSearchParams();
  const token = params.get("token");

  const { data: confirmation, isLoading: confirmationLoading, isError } = useQuery({
    queryKey: ["purchase-confirmation", token],
    queryFn: () => purchaseConfirmationApi.get(token!),
    enabled: !!token,
    retry: false,
  });

  const invalid = !token || isError;

  useEffect(() => {
    if (invalid && !confirmationLoading) {
      navigate("/", { replace: true });
    }
  }, [invalid, confirmationLoading, navigate]);

  if (!token || confirmationLoading) return <PageLoader />;
  if (invalid || !confirmation) return null; // redirecting via the effect above

  const isSubscription = confirmation.type === "subscription";
  const heading = isSubscription ? "Thank you for subscribing!" : "Thank you for your booking!";
  const message = isSubscription
    ? confirmation.payload.plan_name
      ? `Your "${confirmation.payload.plan_name}" subscription is active. You can start booking services with it right away.`
      : "Your subscription is active. You can start booking services with it right away."
    : confirmation.payload.booking_number
      ? `Your booking ${confirmation.payload.booking_number} for ${format(confirmation.payload.scheduled_date!)} · ${confirmation.payload.scheduled_slot} is confirmed.`
      : "Your booking is confirmed. We'll notify you as soon as a captain is assigned.";

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-bg-primary)] p-4">
      <Card className="w-full max-w-md text-center">
        <CardBody className="p-8">
          <span className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-[var(--color-accent-light)] text-[var(--color-success)]">
            {isSubscription ? <Gift className="h-9 w-9" /> : <CheckCircle2 className="h-9 w-9" />}
          </span>
          <h1 className="mt-5 text-2xl font-bold text-[var(--color-text-primary)]">{heading}</h1>
          <p className="mt-2 text-sm text-[var(--color-text-secondary)]">{message}</p>

          <div className="mt-7 flex flex-col gap-2.5">
            {authLoading ? (
              // A fresh page load (e.g. opened straight from a WhatsApp
              // message) re-checks the session before we know which
              // button set is correct — briefly showing "guest" actions to
              // an actually-logged-in customer (or vice versa) would be
              // wrong, so wait the one beat this normally takes.
              <div className="flex justify-center py-2">
                <Spinner />
              </div>
            ) : user ? (
              <>
                <Button className="w-full" onClick={() => navigate(ROLE_HOME[user.role] || "/")}>
                  <LayoutDashboard className="h-4 w-4" /> Return to dashboard
                </Button>
                {isSubscription ? (
                  <Button variant="outline" className="w-full" onClick={() => navigate("/app/subscriptions")}>
                    <Gift className="h-4 w-4" /> View my subscriptions
                  </Button>
                ) : (
                  <Button variant="outline" className="w-full" onClick={() => navigate(`/app/bookings/${confirmation.reference_id}`)}>
                    <ReceiptText className="h-4 w-4" /> View booking details
                  </Button>
                )}
              </>
            ) : (
              <>
                <Button className="w-full" onClick={() => navigate("/login")}>
                  <LogIn className="h-4 w-4" /> Return to login
                </Button>
                <Button variant="outline" className="w-full" onClick={() => navigate("/")}>
                  <Home className="h-4 w-4" /> Return to website
                </Button>
              </>
            )}
          </div>
        </CardBody>
      </Card>
    </div>
  );
}
