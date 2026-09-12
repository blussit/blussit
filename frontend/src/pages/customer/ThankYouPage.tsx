import { useEffect, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
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
  const { user, isLoading: authLoading } = useAuth();
  const [params] = useSearchParams();
  const token = params.get("token");
  const shouldReduceMotion = useReducedMotion();

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
            <motion.p
              custom={0.3}
              variants={contentVariants}
              initial={shouldReduceMotion ? false : "hidden"}
              animate="show"
              className="mt-2 text-sm text-[var(--color-text-secondary)]"
            >
              {message}
            </motion.p>

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
                  {/* Guest-checkout accounts get a random password — the way
                      in is the OTP reset flow, not a password they never had. */}
                  <Button className="w-full" onClick={() => navigate("/forgot-password")}>
                    <LogIn className="h-4 w-4" /> Set your password (OTP)
                  </Button>
                  <Button variant="outline" className="w-full" onClick={() => navigate("/")}>
                    <Home className="h-4 w-4" /> Return to website
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
