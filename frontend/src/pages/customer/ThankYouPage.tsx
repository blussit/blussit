import { useEffect, useMemo, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { motion, useReducedMotion, type Variants } from "framer-motion";
import { CheckCircle2, CreditCard, Gift, Home, LayoutDashboard, LogIn, ReceiptText } from "lucide-react";
import { purchaseConfirmationApi, type PurchaseConfirmation } from "../../api/purchaseConfirmation";
import { Button, Card, CardBody, PageLoader, Spinner } from "../../components/ui";
import { useAuth } from "../../context/AuthContext";
import { useCustomerTheme } from "../../components/customer/useCustomerTheme";
import { format, formatSlot } from "../../lib/date";
import { titleCase } from "../../components/public/landing/shared";
import { retryUnlessClientError } from "../../lib/api-client";
import { readStashedThankYouToken, stashThankYouToken } from "../../lib/thankYou";

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
  const shouldReduceMotion = useReducedMotion();
  useCustomerTheme();
  const instant = location.state as
    | {
        /** The ticket — handed over in router state, never in the URL. */
        token?: string;
        autopay_off?: boolean;
        payment_method?: "cash" | "online";
        type?: "booking" | "subscription";
        booking_number?: string;
        scheduled_date?: string;
        scheduled_slot?: string;
        service_label?: string;
        plan_name?: string;
        service_code?: string | null;
        payment_link?: string | null;
        awaiting_payment?: boolean;
        total_amount?: number;
        cancellation_charge?: number;
        /** Wallet lines (spec 2026-10-07): credit used, a previous balance carried in, what is left to pay. */
        wallet_applied?: number;
        wallet_due_carried?: number;
        amount_due?: number;
      }
    | null;
  // Where the ticket comes from: router state (the booking/plan flow), an
  // old-style ?token= link (moved out of the address bar below — and before
  // any analytics, by index.html), or this tab's copy after a refresh.
  const urlToken = params.get("token");
  const token = urlToken || instant?.token || readStashedThankYouToken();
  useEffect(() => {
    if (!urlToken) return;
    stashThankYouToken(urlToken);
    const rest = new URLSearchParams(params);
    rest.delete("token");
    const query = rest.toString();
    navigate({ pathname: "/thank-you", search: query ? `?${query}` : "" }, { replace: true, state: { ...(instant || {}), token: urlToken } });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlToken]);
  // Asked for auto-pay, but the gateway could only take a one-time payment.
  const autoPayFellBack = !!instant?.autopay_off || params.get("autopay") === "off";

  const { data: confirmation, isLoading: confirmationLoading, isError } = useQuery({
    queryKey: ["purchase-confirmation", token],
    queryFn: () => purchaseConfirmationApi.get(token!),
    enabled: !!token,
    // A blip (network, 5xx) gets two more tries; a token the server
    // rejects (4xx) is final.
    retry: retryUnlessClientError(2),
  });

  // Back from the confirmation must not reopen the booking form that was
  // just used (FE-24): one guard entry, and Back lands on the dashboard (a
  // guest: the homepage) instead.
  const backHome = useRef("/");
  backHome.current = user ? ROLE_HOME[user.role] || "/" : "/";
  useEffect(() => {
    window.history.pushState(window.history.state, "", window.location.href);
    const onBack = () => navigate(backHome.current, { replace: true });
    window.addEventListener("popstate", onBack);
    return () => window.removeEventListener("popstate", onBack);
  }, [navigate]);

  // Home only when there's truly nothing to show: no token, or the server
  // refused it AND this tab didn't arrive straight from the booking/plan
  // flow (whose own result rides along in location.state).
  const invalid = !token || (isError && !instant);

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
  // The booking flow's own label carries the car type ("Sedan · Star Wash");
  // the server's ticket has only the service names, so it's the fallback.
  const ticket = confirmed.payload as PurchaseConfirmation["payload"];
  const ticketCar = !isSubscription ? titleCase(ticket.vehicle_type_name || "") : "";
  const ticketService = titleCase(confirmed.payload.service_label);
  const serviceLabel =
    (!isSubscription && instant?.service_label) ||
    (ticketCar && ticketService && !ticketService.toLowerCase().startsWith(ticketCar.toLowerCase()) ? `${ticketCar} · ${ticketService}` : ticketService);
  const serviceCode = confirmed.payload.service_code || instant?.service_code || null;
  const paymentLink = confirmed.payload.payment_link || instant?.payment_link || null;
  // Awaiting payment is a fact about the booking, link or no link — a
  // failed link creation must never read as "confirmed".
  const awaitingPayment = !!(confirmed.payload.awaiting_payment ?? instant?.awaiting_payment);
  const totalAmount = confirmed.payload.total_amount ?? instant?.total_amount;
  const paymentMethod = ticket.payment_method ?? instant?.payment_method;
  const carriedCharge = Number(ticket.cancellation_charge ?? instant?.cancellation_charge ?? 0) || 0;
  // Wallet lines from the booking just made (router state only).
  const walletCarried = Number(instant?.wallet_due_carried ?? 0) || 0;
  const walletApplied = Number(instant?.wallet_applied ?? 0) || 0;
  const amountDue = typeof instant?.amount_due === "number" ? instant.amount_due : null;
  const walletCovered = walletApplied > 0 && amountDue === 0;
  // What the customer owes and how — shown for every booking, cash included
  // (a cash confirmation used to show no amount at all).
  const paymentLine =
    isSubscription || totalAmount == null
      ? null
      : totalAmount === 0
        ? "Covered by your plan — nothing to pay."
        : walletCovered
          ? "Paid from your Blussit wallet — nothing to pay."
          : awaitingPayment
          ? "Pay online to confirm this booking."
          : paymentMethod === "online"
            ? "Paid online."
            : paymentMethod === "cash"
              ? "Pay after the wash — cash or UPI to the captain."
              : null;
  const heading = isSubscription ? "Your Plan Is Active" : awaitingPayment ? "Pay To Confirm" : "Booking Confirmed";
  const when = confirmed.payload.scheduled_date
    ? `${format(confirmed.payload.scheduled_date)} · ${formatSlot(confirmed.payload.scheduled_slot)}`
    : "";
  const message = isSubscription
    ? `${confirmed.payload.plan_name || "Your plan"} is ready to book with.`
    : awaitingPayment
      ? `Slot held for 30 minutes${when ? `: ${when}` : ""}.`
      : when || "We'll message you when a captain is assigned.";

  return (
    <div className="flex min-h-screen items-center justify-center bg-white p-4">
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
                  className="absolute inset-0 rounded-full bg-gray-100"
                  initial={{ scale: 0.6, opacity: 0.9 }}
                  animate={{ scale: 1.9, opacity: 0 }}
                  transition={{ duration: 0.8, ease: "easeOut" }}
                />
              )}
              <motion.span
                initial={shouldReduceMotion ? false : { scale: 0.4, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                transition={shouldReduceMotion ? undefined : { type: "spring", stiffness: 260, damping: 16, delay: 0.05 }}
                className="relative flex h-16 w-16 items-center justify-center rounded-full bg-gray-100 text-black"
              >
                {isSubscription ? <Gift className="h-9 w-9" /> : <CheckCircle2 className="h-9 w-9" />}
              </motion.span>
            </div>

            <motion.h1
              custom={0.22}
              variants={contentVariants}
              initial={shouldReduceMotion ? false : "hidden"}
              animate="show"
              className="mt-5 text-2xl font-bold text-black"
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
                className="mt-1.5 text-base font-semibold text-black"
              >
                {serviceLabel}
              </motion.p>
            )}
            <motion.p
              custom={0.3}
              variants={contentVariants}
              initial={shouldReduceMotion ? false : "hidden"}
              animate="show"
              className="mt-2 text-sm text-gray-600"
            >
              {message}
            </motion.p>
            {isSubscription && autoPayFellBack && (
              <motion.p
                custom={0.32}
                variants={contentVariants}
                initial={shouldReduceMotion ? false : "hidden"}
                animate="show"
                className="mx-auto mt-3 max-w-xs rounded-xl border border-[#F3E5B5] px-3 py-2 text-xs text-gray-600"
              >
                Auto-pay couldn't be set up — you paid once. You can buy again when it ends.
              </motion.p>
            )}
            {!isSubscription && confirmed.payload.booking_number && (
              <motion.p
                custom={0.34}
                variants={contentVariants}
                initial={shouldReduceMotion ? false : "hidden"}
                animate="show"
                className="mt-1 font-mono-num text-xs text-gray-400"
              >
                {confirmed.payload.booking_number}
              </motion.p>
            )}
            {!isSubscription && totalAmount != null && (
              <motion.div
                custom={0.35}
                variants={contentVariants}
                initial={shouldReduceMotion ? false : "hidden"}
                animate="show"
                className="mt-4 rounded-xl border border-[#E4E9F1] bg-[#F4F8FF] px-4 py-3 text-left"
              >
                <div className="flex items-baseline justify-between gap-3">
                  <span className="text-sm font-medium text-[#5F6878]">Total</span>
                  <span className="font-mono-num text-xl font-bold text-[#0E1A33]">₹{totalAmount}</span>
                </div>
                {walletCarried > 0 ? (
                  <p className="mt-0.5 text-xs text-[#5F6878]" data-testid="thankyou-charge">
                    Includes ₹{Math.round(walletCarried)} previous balance due.
                  </p>
                ) : carriedCharge > 0 ? (
                  <p className="mt-0.5 text-xs text-[#5F6878]" data-testid="thankyou-charge">
                    Includes ₹{Math.round(carriedCharge)} previous cancellation charge.
                  </p>
                ) : null}
                {walletApplied > 0 && (
                  <div className="mt-1.5 space-y-0.5 text-sm" data-testid="thankyou-wallet">
                    <div className="flex items-baseline justify-between gap-3 text-[#1E7B3C]">
                      <span>Wallet Credit</span>
                      <span className="font-mono-num">−₹{Math.round(walletApplied)}</span>
                    </div>
                    {amountDue != null && amountDue > 0 && (
                      <div className="flex items-baseline justify-between gap-3 font-semibold text-[#0E1A33]">
                        <span>To Pay</span>
                        <span className="font-mono-num">₹{Math.round(amountDue)}</span>
                      </div>
                    )}
                  </div>
                )}
                {paymentLine && <p className="mt-0.5 text-xs text-[#5F6878]">{paymentLine}</p>}
              </motion.div>
            )}
            {/* The one number the customer needs on the day: the captain
                asks for it on arrival instead of a registration plate. Also
                sent on WhatsApp with the confirmation. */}
            {!isSubscription && serviceCode && (
              <motion.div
                custom={0.36}
                variants={contentVariants}
                initial={shouldReduceMotion ? false : "hidden"}
                animate="show"
                className="mt-4 rounded-xl border border-[#F3E5B5] bg-white px-4 py-3"
              >
                <p className="text-sm font-medium text-gray-500">Service Code</p>
                <p className="mt-1 tabular-nums text-3xl font-bold tracking-[0.3em] text-[#0A66F0]">{serviceCode}</p>
                <p className="mt-1 text-xs text-gray-500">Share it with the captain on arrival.</p>
              </motion.div>
            )}
            {awaitingPayment && (
              <motion.div custom={0.38} variants={contentVariants} initial={shouldReduceMotion ? false : "hidden"} animate="show" className="mt-4">
                {paymentLink ? (
                  <>
                    <a
                      href={paymentLink}
                      className="flex h-12 w-full items-center justify-center gap-2 rounded-full bg-[#E8A900] text-sm font-semibold text-white hover:bg-[#D99A00]"
                    >
                      <CreditCard className="h-4 w-4" /> Pay {totalAmount ? `₹${totalAmount} ` : ""}Now
                    </a>
                    {/* This tab doesn't refresh itself after the link's own page takes the payment. */}
                    <p className="mt-2 text-xs text-gray-500">Already paid? It confirms automatically — no need to pay again.</p>
                  </>
                ) : (
                  <p className="rounded-xl border border-[#F3E5B5] px-4 py-3 text-xs text-gray-600">
                    Payment link unavailable right now. Open this booking under My Bookings to pay.
                  </p>
                )}
              </motion.div>
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
                  <Button
                    variant={awaitingPayment && paymentLink ? "outline" : "info"}
                    className="w-full"
                    onClick={() => navigate(ROLE_HOME[user.role] || "/")}
                  >
                    <LayoutDashboard className="h-4 w-4" /> Go To Dashboard
                  </Button>
                  {isSubscription ? (
                    <Button variant="outline" className="w-full" onClick={() => navigate("/app/subscriptions")}>
                      <Gift className="h-4 w-4" /> View My Plans
                    </Button>
                  ) : (
                    <Button variant="outline" className="w-full" disabled={!confirmed.reference_id} onClick={() => navigate(`/app/bookings/${confirmed.reference_id}`)}>
                      <ReceiptText className="h-4 w-4" /> View Booking
                    </Button>
                  )}
                </>
              ) : (
                <>
                  {/* Quick-booking accounts have no password — logging in is
                      a phone OTP, only if they ever want to see history. */}
                  <Button variant={awaitingPayment && paymentLink ? "outline" : "info"} className="w-full" onClick={() => navigate("/login")}>
                    <LogIn className="h-4 w-4" /> Log In To Track It
                  </Button>
                  <Button variant="ghost" className="w-full" onClick={() => navigate("/")}>
                    <Home className="h-4 w-4" /> Back To Website
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
