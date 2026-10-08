import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, CheckCircle2, IndianRupee } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { TIER_LABELS, type LateCancellationCharge } from "../../api/charges";
import { staffBookingApi, type CancelWalletResult } from "../../api/staffBookings";
import { minutesUntilSlotStart } from "../../lib/date";
import { getErrorMessage } from "../../lib/api-client";
import { useToast } from "../../context/ToastContext";
import { Button, Input, Modal } from "../ui";
import type { Booking } from "../../types";

/**
 * Staff cancel — booking queue, booking detail, admin bookings. A visit is
 * one decision, so a car on a visit cancels the whole visit (as the queue
 * always did).
 *
 * Founder rule (2026-10-07): only a cancellation the CUSTOMER asked for can
 * carry the late-cancellation charge, and it is settled through their
 * WALLET (netted against anything paid; a debit otherwise — the next
 * booking carries it). The answer defaults to "No" (a business cancel
 * never charges); "Yes" shows the policy tier and its amount, which staff
 * may lower (never raise) before confirming. A plan-covered wash cancelled
 * inside its last hour is used up unless staff tick "Return The Plan Wash".
 * The result shows what happened to the wallet.
 */
export function StaffCancelDialog({
  booking,
  onClose,
  onCancelled,
}: {
  booking: Booking | null;
  onClose: () => void;
  /** After a successful cancel (before the result view is dismissed). */
  onCancelled?: () => void;
}) {
  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const isVisit = !!booking?.booking_group_id;
  const [reason, setReason] = useState("");
  const [atRequest, setAtRequest] = useState(false);
  const [amount, setAmount] = useState("");
  const [error, setError] = useState("");
  const [returnWash, setReturnWash] = useState(false);
  const [result, setResult] = useState<{
    number: string;
    charge: LateCancellationCharge | null;
    wallet: CancelWalletResult | null;
    forfeited: boolean;
  } | null>(null);

  // A fresh dialog for every booking.
  useEffect(() => {
    setReason("");
    setAtRequest(false);
    setReturnWash(false);
    setAmount("");
    setError("");
    setResult(null);
  }, [booking?.id]);

  const preview = useQuery({
    queryKey: ["cancellation-charge-preview", booking?.id, isVisit],
    queryFn: () => bookingApi.cancellationChargePreview(booking!.id, isVisit),
    enabled: !!booking && !result,
    // The tier moves with the clock (and a captain leaving) — always fresh.
    staleTime: 0,
    retry: 1,
  });
  const computed = Math.round(preview.data?.amount ?? 0);
  // The amount box starts at the policy amount once it's known.
  useEffect(() => {
    if (preview.data) setAmount(String(Math.round(preview.data.amount)));
  }, [preview.data]);

  const typed = amount.trim() === "" ? NaN : Math.round(Number(amount));
  const amountValid = Number.isFinite(typed) && typed >= 0 && typed <= computed;
  const chargeApplies = atRequest && computed > 0;
  const reasonValid = reason.trim().length >= 3;
  // "Yes" needs the server's tier first — never a charge we didn't show.
  const ready = reasonValid && (!atRequest || (!!preview.data && (!chargeApplies || amountValid)));

  // A plan wash inside its last hour is used up by default — staff may hand it back.
  const minutesToSlot = preview.data?.minutes_to_slot ?? (booking ? minutesUntilSlotStart(booking.scheduled_date, booking.scheduled_slot) : Infinity);
  const planInsideHour = !!booking?.subscription_id && minutesToSlot < 60;

  const cancel = useMutation({
    mutationFn: async () => {
      const input = {
        reason: reason.trim(),
        ...(atRequest ? { at_customer_request: true, ...(chargeApplies ? { charge_amount: typed } : {}) } : {}),
        ...(planInsideHour ? { return_plan_wash: returnWash } : {}),
      };
      return booking!.booking_group_id ? staffBookingApi.cancelGroup(booking!.booking_group_id, input) : staffBookingApi.cancel(booking!.id, input);
    },
    onSuccess: (r) => {
      const charge = r.late_cancellation_charge ?? null;
      const wallet = r.wallet ?? null;
      const forfeited = !!r.plan_wash_forfeited;
      queryClient.invalidateQueries({
        predicate: (q) =>
          typeof q.queryKey[0] === "string" &&
          /^(center-bookings|admin-center-bookings|admin-bookings|booking|manager-dashboard|manager-kpi|charges|customer-360|cancellation-charge-preview)/.test(q.queryKey[0]),
      });
      onCancelled?.();
      queryClient.invalidateQueries({ queryKey: ["customer-wallet"] });
      if ((charge && charge.amount > 0) || (wallet && (wallet.credited || wallet.charge || wallet.net)) || forfeited) {
        setResult({ number: booking!.booking_number, charge, wallet, forfeited });
        return;
      }
      pushToast({ tone: "success", title: isVisit ? "Visit Cancelled" : "Booking Cancelled", message: atRequest ? "No charge added." : undefined });
      onClose();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const title = result ? "Cancelled" : isVisit ? "Cancel Visit" : "Cancel Booking";
  const paidOnline = booking?.payment_status === "paid" && booking.payment_method === "online";

  return (
    <Modal open={!!booking} onClose={onClose} title={title} maxWidth="max-w-md">
      {booking && result ? (
        <div className="space-y-4" data-testid="staff-cancel-result">
          <div className="flex items-start gap-3 rounded-xl bg-[var(--color-primary-light)] p-3.5">
            <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-[var(--color-success)]" />
            <div className="min-w-0 space-y-1 text-sm text-[#0E1A33]">
              <p className="font-semibold">
                <span className="font-mono-num">{result.number}</span> Is Cancelled
              </p>
              {result.charge && result.charge.amount > 0 && (
                <p className="text-[#5F6878]">
                  <span className="font-mono-num font-semibold text-[#0E1A33]">₹{Math.round(result.charge.amount)}</span> late-cancellation charge (
                  {TIER_LABELS[result.charge.tier] || result.charge.tier}) — added to {booking.customer_name || "the customer"}&apos;s wallet as due.
                </p>
              )}
              {result.wallet && (
                <div className="text-[#5F6878]" data-testid="staff-cancel-wallet">
                  {result.wallet.credited ? (
                    <p>
                      Credited <span className="font-mono-num font-semibold text-[var(--color-success)]">₹{Math.round(result.wallet.credited)}</span> to the wallet.
                    </p>
                  ) : null}
                  {result.wallet.wallet_line ? <p>{result.wallet.wallet_line}</p> : null}
                  {result.wallet.balance != null && (
                    <p>
                      Wallet now{" "}
                      <span className={`font-mono-num font-semibold ${result.wallet.balance < 0 ? "text-[var(--color-error)]" : "text-[#0E1A33]"}`}>
                        {result.wallet.balance < 0 ? "−" : ""}₹{Math.abs(Math.round(result.wallet.balance))}
                      </span>
                      .
                    </p>
                  )}
                </div>
              )}
              {result.forfeited && <p className="text-[#5F6878]">The plan wash was used up (cancelled inside its last hour).</p>}
            </div>
          </div>
          <Button className="min-h-11 w-full" onClick={onClose}>
            Done
          </Button>
        </div>
      ) : booking ? (
        <div className="space-y-4">
          <p className="text-sm text-[var(--color-text-secondary)]">
            {isVisit
              ? "This cancels every vehicle on this visit — the customer and captain (if assigned) are notified."
              : "This cancels the booking — the customer and captain (if assigned) are notified."}
            {paidOnline ? " What was paid goes back to the customer's wallet (less any charge)." : ""}
          </p>
          <textarea
            aria-label="Reason"
            className="w-full min-w-0 rounded-xl border border-gray-300 px-3.5 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--color-primary)]"
            rows={3}
            placeholder="Reason (min 3 characters)"
            maxLength={300}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />

          <fieldset>
            <legend className="mb-2 text-sm font-semibold text-[#0E1A33]">Cancelled At The Customer&apos;s Request?</legend>
            <div className="grid grid-cols-2 gap-2" role="radiogroup">
              {[
                { value: false, label: "No", sub: "Our side — no charge" },
                { value: true, label: "Yes", sub: "Customer asked" },
              ].map((o) => {
                const on = atRequest === o.value;
                return (
                  <button
                    key={o.label}
                    type="button"
                    role="radio"
                    aria-checked={on}
                    onClick={() => {
                      setAtRequest(o.value);
                      setError("");
                    }}
                    className={`min-w-0 rounded-xl border px-3 py-2.5 text-left transition-colors ${
                      on ? "border-black bg-[var(--color-primary-light)] ring-1 ring-black" : "border-gray-200 bg-white hover:border-gray-300"
                    }`}
                  >
                    <span className="block text-sm font-semibold text-[#0E1A33]">{o.label}</span>
                    <span className="block text-xs text-[#5F6878]">{o.sub}</span>
                  </button>
                );
              })}
            </div>
          </fieldset>

          {atRequest && (
            <div className="rounded-xl border border-[#E4E9F1] bg-[#F7F9FC] p-3.5" data-testid="staff-cancel-charge">
              {preview.isLoading ? (
                <p className="text-sm text-[#5F6878]">Checking the cancellation charge…</p>
              ) : preview.isError || !preview.data ? (
                <p className="text-sm text-[#5F6878]">
                  Couldn&apos;t check the cancellation charge.{" "}
                  <button type="button" className="font-semibold text-[#0A66F0] hover:underline disabled:opacity-60" disabled={preview.isFetching} onClick={() => void preview.refetch()}>
                    {preview.isFetching ? "Trying…" : "Try Again"}
                  </button>
                </p>
              ) : computed <= 0 ? (
                <p className="text-sm text-[#0E1A33]">
                  <span className="font-semibold">Free Cancellation</span> — it&apos;s more than {preview.data.window_hours} hours before the slot, so nothing is added.
                </p>
              ) : (
                <div className="space-y-3">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                    <span className="text-sm text-[#5F6878]">{TIER_LABELS[preview.data.tier] || preview.data.tier}</span>
                    <span className="font-mono-num text-sm font-semibold text-[#0E1A33]">Policy ₹{computed}</span>
                  </div>
                  <Input
                    label="Charge To Add (₹)"
                    inputMode="numeric"
                    value={amount}
                    onChange={(e) => setAmount(e.target.value.replace(/\D/g, "").slice(0, 4))}
                    error={amount.trim() !== "" && !amountValid ? `At most ₹${computed}.` : undefined}
                    hint={`Reduce it if you like (0 = no charge) — it can't be more than ₹${computed}.`}
                  />
                  <p className="flex items-start gap-1.5 text-xs text-[#5F6878]">
                    <IndianRupee className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    Not collected now — it goes on the customer&apos;s wallet (taken from what they paid, or added to their next booking).
                  </p>
                </div>
              )}
            </div>
          )}

          {planInsideHour && (
            <label className="flex min-h-11 cursor-pointer items-start gap-3 rounded-xl border border-gray-200 p-3" data-testid="return-plan-wash">
              <input type="checkbox" className="mt-0.5 h-5 w-5 shrink-0 accent-black" checked={returnWash} onChange={(e) => setReturnWash(e.target.checked)} />
              <span>
                <span className="block text-sm font-semibold text-[#0E1A33]">Return The Plan Wash</span>
                <span className="block text-xs text-[#5F6878]">It&apos;s inside the last hour, so the wash is used up unless you return it.</span>
              </span>
            </label>
          )}
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <div className="flex gap-2">
            <Button variant="outline" className="flex-1" onClick={onClose}>
              Back
            </Button>
            <Button variant="danger" className="flex-1" isLoading={cancel.isPending} disabled={!ready} onClick={() => cancel.mutate()}>
              <Ban className="h-4 w-4" /> {isVisit ? "Cancel Visit" : "Cancel Booking"}
            </Button>
          </div>
        </div>
      ) : null}
    </Modal>
  );
}
