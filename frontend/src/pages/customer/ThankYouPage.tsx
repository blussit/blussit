import { useEffect, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { motion, useReducedMotion, type Variants } from "framer-motion";
import { CheckCircle2, Gift, Home, LayoutDashboard, LogIn, ReceiptText } from "lucide-react";
import { purchaseConfirmationApi } from "../../api/purchaseConfirmation";
import { Button, Card, CardBody, PageLoader, Spinner } from "../../components/ui";
import { useAuth } from "../../context/AuthContext";
import { format } from "../../lib/date";

const ROLE_HOME: Record<string, string> = { customer: "/app", manager: "/manager", admin: "/admin", captain: "/captain" };

/** A handful of small dots that pop out from the icon and settle — the
 *  "order placed" flourish, kept brief and brand-gold rather than a full
 *  confetti shower, since a classic checkout page still has to read as
 *  professional a second after the celebration. Skipped entirely under
 *  reduced-motion. */
function CelebrationBurst({ reduced }: { reduced: boolean }) {
  const particles = useMemo(
    () =>
      Array.from({ length: 10 }, (_, i) => {
        const angle = (i / 10) * Math.PI * 2 + (i % 2 ? 0.25 : -0.25);
        const distance = 46 + (i % 3) * 10;
        return { x: Math.cos(angle) * distance, y: Math.sin(angle) * distance, delay: 0.32 + (i % 4) * 0.03 };
      }),
    []
  );
  if (reduced) return null;
  return (
    <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
      {particles.map((p, i) => (
        <motion.span
          key={i}
          className="absolute h-1.5 w-1.5 rounded-full bg-[#E8A900]"
          initial={{ x: 0, y: 0, opacity: 0, scale: 0.4 }}
          animate={{ x: p.x, y: p.y, opacity: [0, 1, 0], scale: 1 }}
          transition={{ duration: 0.7, delay: p.delay, ease: "easeOut" }}
        />
      ))}
    </div>
  );
}

const contentVariants: Variants = {
  hidden: { opacity: 0, y: 10 },
  show: (delay: number) => ({ opacity: 1, y: 0, transition: { duration: 0.4, delay, ease: "easeOut" } }),
};

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
  const location = useLocation();
  const { user, isLoading: authLoading } = useAuth();
  const [params] = useSearchParams();
  const token = params.get("token");
  const shouldReduceMotion = useReducedMotion();
  const instant = location.state as
    | {
        type?: "booking" | "subscription";
        booking_number?: string;
        scheduled_date?: string;
        scheduled_slot?: string;
        service_label?: string;
        plan_name?: string;
      }
    | null;

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

  if (!token) return <PageLoader />;
  if (confirmationLoading && !instant) return <PageLoader />;
  if (invalid || (!confirmation && !instant)) return null; // redirecting via the effect above

  const confirmed = confirmation || { type: instant?.type || "booking", payload: instant || {}, reference_id: "" };
  const isSubscription = confirmed.type === "subscription";
  const serviceLabel = confirmed.payload.service_label;
  const heading = isSubscription ? "Thank You For Subscribing!" : "Thank You For Your Booking!";
  const message = isSubscription
    ? confirmed.payload.plan_name
      ? `Your ${confirmed.payload.plan_name} Subscription Is Active. You Can Start Booking Services With It Right Away.`
      : "Your Subscription Is Active. You Can Start Booking Services With It Right Away."
    : confirmed.payload.booking_number
      ? `Scheduled For ${format(confirmed.payload.scheduled_date!)} · ${confirmed.payload.scheduled_slot}.`
      : "Your Booking Is Confirmed. We'll Notify You As Soon As A Captain Is Assigned.";

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-bg-primary)] p-4">
      <motion.div
        initial={shouldReduceMotion ? false : { opacity: 0, y: 16, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.45, ease: "easeOut" }}
        className="w-full max-w-md"
      >
        <Card className="text-center">
          <CardBody className="p-8">
            <div className="relative mx-auto flex h-16 w-16 items-center justify-center">
              <CelebrationBurst reduced={!!shouldReduceMotion} />
              {/* One soft ring pulse behind the icon — settles immediately,
                  never loops, so the page reads calm a beat later. */}
              {!shouldReduceMotion && (
                <motion.span
                  className="absolute inset-0 rounded-full bg-[var(--color-accent-light)]"
                  initial={{ scale: 0.6, opacity: 0.9 }}
                  animate={{ scale: 1.9, opacity: 0 }}
                  transition={{ duration: 0.8, ease: "easeOut" }}
                />
              )}
              <motion.span
                initial={shouldReduceMotion ? false : { scale: 0.4, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                transition={shouldReduceMotion ? undefined : { type: "spring", stiffness: 260, damping: 16, delay: 0.05 }}
                className="relative flex h-16 w-16 items-center justify-center rounded-full bg-[var(--color-accent-light)] text-[var(--color-success)]"
              >
                {isSubscription ? <Gift className="h-9 w-9" /> : <CheckCircle2 className="h-9 w-9" />}
              </motion.span>
            </div>

            <motion.h1
              custom={0.22}
              variants={contentVariants}
              initial={shouldReduceMotion ? false : "hidden"}
              animate="show"
              className="mt-5 text-2xl font-bold text-[var(--color-text-primary)]"
            >
              {heading}
            </motion.h1>
            {/* Service name reads as the headline fact of the booking; the
                booking number is a receipt detail, not a headline — shown
                small underneath instead of buried mid-sentence. */}
            {!isSubscription && serviceLabel && (
              <motion.p
                custom={0.26}
                variants={contentVariants}
                initial={shouldReduceMotion ? false : "hidden"}
                animate="show"
                className="mt-1.5 text-base font-semibold text-[var(--color-text-primary)]"
              >
                {serviceLabel}
              </motion.p>
            )}
            <motion.p
              custom={0.3}
              variants={contentVariants}
              initial={shouldReduceMotion ? false : "hidden"}
              animate="show"
              className="mt-2 text-sm text-[var(--color-text-secondary)]"
            >
              {message}
            </motion.p>
            {!isSubscription && confirmed.payload.booking_number && (
              <motion.p
                custom={0.34}
                variants={contentVariants}
                initial={shouldReduceMotion ? false : "hidden"}
                animate="show"
                className="mt-1 font-mono-num text-xs text-[var(--color-text-secondary)]"
              >
                Booking ID: {confirmed.payload.booking_number}
              </motion.p>
            )}

            <motion.div
              custom={0.4}
              variants={contentVariants}
              initial={shouldReduceMotion ? false : "hidden"}
              animate="show"
              className="mt-7 flex flex-col gap-2.5"
            >
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
                    <LayoutDashboard className="h-4 w-4" /> Return To Dashboard
                  </Button>
                  {isSubscription ? (
                    <Button variant="outline" className="w-full" onClick={() => navigate("/app/subscriptions")}>
                      <Gift className="h-4 w-4" /> View My Subscriptions
                    </Button>
                  ) : (
                    <Button variant="outline" className="w-full" disabled={!confirmed.reference_id} onClick={() => navigate(`/app/bookings/${confirmed.reference_id}`)}>
                      <ReceiptText className="h-4 w-4" /> View Booking Details
                    </Button>
                  )}
                </>
              ) : (
                <>
                  {/* Guest-checkout accounts get a random password — the way
                      in is the OTP reset flow, not a password they never had. */}
                  <Button className="w-full" onClick={() => navigate("/forgot-password")}>
                    <LogIn className="h-4 w-4" /> Set Your Password (OTP)
                  </Button>
                  <Button variant="outline" className="w-full" onClick={() => navigate("/")}>
                    <Home className="h-4 w-4" /> Return To Website
                  </Button>
                </>
              )}
            </motion.div>
          </CardBody>
        </Card>
      </motion.div>
    </div>
  );
}
