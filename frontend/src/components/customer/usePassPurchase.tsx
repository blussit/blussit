import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { PaymentCancelled, PaymentFailed, PaymentNeedsAttention, PaymentPendingConfirmation, paymentErrorMessage, payWithRazorpay } from "../../lib/razorpay";
import type { SubscriptionPlan } from "../../types";
import { PassPurchaseSheet, type PassPrefill } from "./PassPurchaseSheet";

const AUTO_PAY_FALLBACK = "Auto-pay couldn't be set up — you paid once. You can buy again when it ends.";

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
  // The sheet (and `plan`) can close mid-checkout — keep the name for the note.
  const buyingName = useRef<string | undefined>(undefined);

  const invalidateSubs = () => queryClient.invalidateQueries({ queryKey: ["my-subscriptions"] });

  const mutation = useMutation({
    mutationFn: (args: { vehicleType: string; serviceId: string; autoPay: boolean }) => {
      setError("");
      setNote("");
      autoPayUnavailable.current = false;
      buyingName.current = plan?.name;
      return payWithRazorpay(
        { purpose: "subscription", plan_id: plan!.id, vehicle_type: args.vehicleType, service_id: args.serviceId, auto_pay: args.autoPay },
        { name: user?.full_name, email: user?.email, contact: user?.phone },
        (created) => {
          autoPayUnavailable.current = !!created.auto_pay_unavailable;
        },
        {
          onConfirming: () => {
            setPlan(null);
            setNote("Payment done — confirming it with the bank…");
          },
          onFailed: (reason) => setError(`Payment failed — ${reason}`),
        }
      );
    },
    onSuccess: (result) => {
      invalidateSubs();
      const planName = result.subscription?.plan_name || buyingName.current;
      const fallback = autoPayUnavailable.current;
      setPlan(null);
      setError("");
      // The thank-you ticket goes to whichever request confirmed the
      // purchase first; a later confirmation just shows the pass here.
      if (result.confirmation_token) {
        const query = new URLSearchParams({ token: result.confirmation_token });
        if (planName) query.set("plan", planName);
        if (fallback) query.set("autopay", "off");
        navigate(`/thank-you?${query.toString()}`);
      } else {
        setNote(`Payment received — ${planName || "your pass"} is active.${fallback ? ` ${AUTO_PAY_FALLBACK}` : ""}`);
      }
    },
    onError: (err) => {
      invalidateSubs();
      if (err instanceof PaymentPendingConfirmation || err instanceof PaymentNeedsAttention) {
        setPlan(null);
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

  return { start, sheet, note, clearNote: () => setNote(""), isPaying: mutation.isPending };
}
