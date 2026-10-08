import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { PaymentCancelled, PaymentFailed, PaymentNeedsAttention, PaymentPendingConfirmation, paymentErrorMessage, payWithRazorpay } from "../../lib/razorpay";
import { trackInitiateCheckout, trackPurchase } from "../../lib/metaPixel";
import { stashThankYouToken } from "../../lib/thankYou";
import type { SubscriptionPlan } from "../../types";
import { PassPurchaseSheet, type PassPrefill } from "./PassPurchaseSheet";

const AUTO_PAY_FALLBACK = "Auto-pay couldn't be set up — you paid once. You can buy again when it ends.";

// A pass payment that is confirming / under review blocks buying again for
// a while (this tab, surviving a refresh) — a second checkout was accepted
// and parked for a refund. The server refuses one too (PAYMENT_CONFIRMING).
const HOLD_KEY = "blussit:pass-payment-pending";
const HOLD_MS = 5 * 60 * 1000;
const HELD_NOTE = "Your last plan payment is still being confirmed — no need to pay again. It shows under My Plans in a few minutes.";
function readHold(): number {
  try {
    const at = Number(sessionStorage.getItem(HOLD_KEY) || 0);
    return at && Date.now() - at < HOLD_MS ? at : 0;
  } catch {
    return 0;
  }
}
function writeHold(at: number) {
  try {
    if (at) sessionStorage.setItem(HOLD_KEY, String(at));
    else sessionStorage.removeItem(HOLD_KEY);
  } catch {
    // storage blocked — the in-memory hold still applies
  }
}

/**
 * Buying a monthly pass, end to end — the sheet, Razorpay checkout and the
 * outcome — shared by the Subscriptions page and the dashboard's "Buy again".
 * Render `sheet` once; show `note` (the result once the sheet has closed)
 * wherever the page wants it.
 */
export function usePassPurchase() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const queryClient = useQueryClient();

  const [plan, setPlan] = useState<SubscriptionPlan | null>(null);
  const [prefill, setPrefill] = useState<PassPrefill | null>(null);
  const [error, setError] = useState("");
  // After checkout closes: "confirming…", "your pass is active", or "under
  // review" — the sheet is closed by then, so the page shows it.
  const [note, setNote] = useState("");
  // Auto-pay can silently become a one-time payment when the gateway won't
  // set up a mandate — the customer must be told which one they bought.
  const autoPayUnavailable = useRef(false);
  const [heldAt, setHeldAt] = useState(readHold);
  const held = heldAt > 0;
  const hold = (at: number) => {
    setHeldAt(at);
    writeHold(at);
  };
  // Release when the window passes; meanwhile keep the plans list fresh so
  // the confirmed pass appears by itself.
  useEffect(() => {
    if (!heldAt) return;
    const poll = window.setInterval(() => void queryClient.invalidateQueries({ queryKey: ["my-subscriptions"] }), 15_000);
    const release = window.setTimeout(() => hold(0), Math.max(0, heldAt + HOLD_MS - Date.now()));
    return () => {
      window.clearInterval(poll);
      window.clearTimeout(release);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [heldAt]);
  // The sheet (and `plan`) can close mid-checkout — keep the name for the note.
  const buyingName = useRef<string | undefined>(undefined);

  const invalidateSubs = () => queryClient.invalidateQueries({ queryKey: ["my-subscriptions"] });

  const mutation = useMutation({
    mutationFn: (args: { vehicleType: string; serviceId: string; autoPay: boolean }) => {
      setError("");
      setNote("");
      autoPayUnavailable.current = false;
      buyingName.current = plan?.name;
      trackInitiateCheckout({ contentType: "plan", contentName: plan?.name });
      return payWithRazorpay(
        { purpose: "subscription", plan_id: plan!.id, vehicle_type: args.vehicleType, service_id: args.serviceId, auto_pay: args.autoPay },
        { name: user?.full_name, email: user?.email, contact: user?.phone },
        (created) => {
          autoPayUnavailable.current = !!created.auto_pay_unavailable;
        },
        {
          onConfirming: () => {
            setPlan(null);
            hold(Date.now());
            setNote("Payment done — confirming it with the bank…");
          },
          onFailed: (reason) => setError(`Payment failed — ${reason}`),
        }
      );
    },
    onSuccess: (result) => {
      invalidateSubs();
      hold(0);
      const planName = result.subscription?.plan_name || buyingName.current;
      const fallback = autoPayUnavailable.current;
      const paid = result.subscription?.purchased_price;
      if (result.subscription && paid && paid > 0) {
        trackPurchase({ value: paid, eventId: `plan:${result.subscription.id}`, contentType: "plan", contentName: planName });
      }
      setPlan(null);
      setError("");
      // The thank-you ticket goes to whichever request confirmed the
      // purchase first; a later confirmation just shows the pass here.
      if (result.confirmation_token) {
        // The ticket rides in router state + this tab's storage, never the
        // URL (analytics record every URL — see lib/thankYou.ts).
        stashThankYouToken(result.confirmation_token);
        navigate("/thank-you", {
          state: { token: result.confirmation_token, type: "subscription", plan_name: planName || undefined, autopay_off: fallback || undefined },
        });
      } else {
        setNote(`Payment received — ${planName || "your pass"} is active.${fallback ? ` ${AUTO_PAY_FALLBACK}` : ""}`);
      }
    },
    onError: (err) => {
      invalidateSubs();
      if (err instanceof PaymentPendingConfirmation || err instanceof PaymentNeedsAttention) {
        setPlan(null);
        hold(Date.now());
        setNote(err.message);
        return;
      }
      if (err instanceof PaymentFailed) {
        setError(`Payment failed — ${err.reason} You can try again.`);
        return;
      }
      // A pass only exists once the payment verifies, so an abandoned
      // checkout has bought nothing — say so rather than closing silently.
      if (err instanceof PaymentCancelled) {
        setError("Payment wasn't completed, so your pass hasn't started. You can try again.");
        return;
      }
      setNote("");
      setError(paymentErrorMessage(err));
    },
  });

  const start = (next: SubscriptionPlan, nextPrefill?: PassPrefill | null) => {
    if (held || mutation.isPending) {
      setNote(HELD_NOTE);
      return;
    }
    setPrefill(nextPrefill ?? null);
    setError("");
    setPlan(next);
  };

  const sheet = (
    <PassPurchaseSheet
      plan={plan}
      open={!!plan}
      onClose={() => setPlan(null)}
      onConfirm={(args) => mutation.mutate(args)}
      isPaying={mutation.isPending}
      error={error}
      prefill={prefill}
    />
  );

  /** A payment is in flight or being confirmed — buy buttons should be off. */
  const purchaseHeld = held || mutation.isPending;
  return { start, sheet, note, clearNote: () => setNote(""), isPaying: mutation.isPending, purchaseHeld };
}
