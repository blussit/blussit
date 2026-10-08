import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, HandCoins } from "lucide-react";
import {
  customerWalletApi,
  newIdempotencyKey,
  PAYBACK_REASON_LABELS,
  PAYOUT_METHOD_LABELS,
  type PaybackReason,
  type PaybackResult,
  type PayoutMethod,
} from "../../api/customerWallet";
import { moneyOf, type StaffBooking } from "../../api/staffBookings";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";
import { formatShortDate, formatSlot } from "../../lib/date";
import { bookingCarAndService, toTitle } from "../../lib/titleCase";
import { Button, Input, Modal } from "../ui";

/**
 * Pay Back To Customer (founder 2026-10-07, MONEY-2). A manager never adds
 * money to a wallet at will: he pays money back — from his own side, cash or
 * online — only for ONE booking that was cancelled, delayed or had an issue,
 * and records it here ("Paid By Manager"). The server takes the customer's
 * wallet credit first; the rest is recorded as paid by the manager. It
 * decides eligibility and the limit (what the customer paid); its 400
 * message is shown as-is.
 */

const rupees = (n: number) => `₹${Math.round(Math.abs(n)).toLocaleString("en-IN")}`;
const REASONS: PaybackReason[] = ["cancelled", "delayed", "complaint", "service_issue", "other"];
const METHODS: PayoutMethod[] = ["cash", "upi", "bank_transfer", "other"];
const DELAY_WORDS = ["late", "delay", "overrun", "missed", "not_started", "not_reached"];

/** A booking the payback can be for, as the dialog lists it. */
export interface PaybackBookingOption {
  id: string;
  booking_number: string;
  /** "Hatchback · Star Wash". */
  title: string;
  /** "7 Oct · 9:00 AM – 12:00 PM". */
  when?: string;
  status: string;
  /** What the customer paid for it (best estimate — the server checks). */
  paid: number;
  paidBack: number;
  /** Why money may go back (client hint — the server decides). */
  signals: PaybackReason[];
}

/** cancelled / delayed / service_issue / complaint — the same signals the
 *  server checks (payback_signals), minus what only it can see. */
export function paybackSignals(b: StaffBooking, hasComplaint = false): PaybackReason[] {
  const raw = b as StaffBooking & { resolved_issue_flag?: string | null; late_penalty_amount?: number | null };
  const out: PaybackReason[] = [];
  if (b.status === "cancelled") out.push("cancelled");
  const flags = [b.issue_flag, raw.resolved_issue_flag].filter(Boolean).map(String);
  const lateFlag = flags.some((f) => DELAY_WORDS.some((w) => f.includes(w)));
  if ((Number(b.delay_minutes) || 0) > 0 || (Number(raw.late_penalty_amount) || 0) > 0 || (Number(b.late_penalty_pct) || 0) > 0 || lateFlag) out.push("delayed");
  if (hasComplaint) out.push("complaint");
  if (flags.length) out.push("service_issue");
  return out;
}

export function paybackOption(b: StaffBooking, hasComplaint = false): PaybackBookingOption {
  const m = moneyOf(b);
  const named = b as StaffBooking & { vehicle_label?: string | null; service_names?: string[] | null; combo_name?: string | null };
  return {
    id: b.id,
    booking_number: b.booking_number,
    title: bookingCarAndService(named) || toTitle(named.vehicle_type_name) || "Booking",
    when: [b.scheduled_date ? formatShortDate(b.scheduled_date) : "", b.scheduled_slot ? formatSlot(b.scheduled_slot) : ""].filter(Boolean).join(" · "),
    status: b.status,
    paid: Math.max(m.paid + m.wallet, Number(b.refunded_amount) || 0),
    paidBack: Number(b.paid_back_total) || 0,
    signals: paybackSignals(b, hasComplaint),
  };
}

/** "₹50 From Wallet + ₹150 Paid By You" (or one of the halves). */
export function paybackSplitLine(walletPart: number, byYou: number, you = "You"): string {
  const parts = [];
  if (walletPart > 0) parts.push(`${rupees(walletPart)} From Wallet`);
  if (byYou > 0) parts.push(`${rupees(byYou)} Paid By ${you}`);
  return parts.join(" + ") || "₹0";
}

export function PaybackDialog({
  open,
  onClose,
  customerId,
  customerName,
  bookings,
  defaultBookingId,
  loadingBookings = false,
}: {
  open: boolean;
  onClose: () => void;
  customerId: string;
  customerName?: string | null;
  /** The bookings it can be for (eligible ones first). */
  bookings: PaybackBookingOption[];
  defaultBookingId?: string | null;
  loadingBookings?: boolean;
}) {
  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const [bookingId, setBookingId] = useState("");
  const [reason, setReason] = useState<PaybackReason>("other");
  const [method, setMethod] = useState<PayoutMethod>("cash");
  const [reference, setReference] = useState("");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [key, setKey] = useState(() => newIdempotencyKey("pb"));
  const [done, setDone] = useState<PaybackResult | null>(null);

  // Wallet credit decides the split (same page the wallet panel reads).
  const wallet = useQuery({
    queryKey: ["customer-wallet", customerId, 1],
    queryFn: () => customerWalletApi.forCustomer(customerId, { page: 1, page_size: 8 }),
    enabled: open && !!customerId,
  });
  const credit = Math.max(0, Math.floor(wallet.data?.balance ?? 0));

  const picked = useMemo(() => bookings.find((b) => b.id === bookingId) || null, [bookings, bookingId]);
  const left = picked ? Math.max(0, Math.floor(picked.paid - picked.paidBack)) : 0;

  // Fresh form + idempotency key every time the dialog opens.
  useEffect(() => {
    if (!open) return;
    const first = bookings.find((b) => b.id === defaultBookingId) || (bookings.length === 1 ? bookings[0] : null);
    setBookingId(first?.id || "");
    setReason(first?.signals[0] || "other");
    setMethod("cash");
    setReference("");
    setAmount("");
    setNote("");
    setError("");
    setDone(null);
    setKey(newIdempotencyKey("pb"));
    // Only the open edge resets the form (bookings may arrive later).
  }, [open]);
  // The booking list loaded after opening: pick the default then.
  useEffect(() => {
    if (!open || bookingId || !bookings.length) return;
    const first = bookings.find((b) => b.id === defaultBookingId) || (bookings.length === 1 ? bookings[0] : null);
    if (first) {
      setBookingId(first.id);
      setReason(first.signals[0] || "other");
    }
  }, [open, bookings, bookingId, defaultBookingId]);

  const value = amount.trim() === "" ? NaN : Math.round(Number(amount));
  const amountOk = Number.isFinite(value) && value >= 1;
  const refOk = method === "cash" || reference.trim().length >= 3;
  const noteOk = reason !== "other" || note.trim().length >= 10;
  const fromWallet = amountOk ? Math.min(value, credit) : 0;
  const byYou = amountOk ? value - fromWallet : 0;
  const cancelledShort = picked?.status === "cancelled" && byYou > 0;

  const save = useMutation({
    mutationFn: () =>
      customerWalletApi.payback(customerId, {
        booking_id: bookingId,
        reason,
        method,
        reference: method === "cash" ? undefined : reference,
        amount: value,
        note,
        idempotency_key: key,
      }),
    onSuccess: ({ result, message }) => {
      queryClient.invalidateQueries({ queryKey: ["customer-wallet", customerId] });
      queryClient.invalidateQueries({
        predicate: (q) =>
          typeof q.queryKey[0] === "string" &&
          /^(wallet-payouts|booking-group|staff-booking|center-bookings|admin-bookings|admin-center-bookings|customer-360|collections|center-collections|admin-collections)/.test(q.queryKey[0]),
      });
      const split = paybackSplitLine(result.wallet_amount, result.goodwill_amount);
      pushToast({
        tone: result.created === false ? "info" : "success",
        title: result.created === false ? "Already Recorded" : `Paid Back ${rupees(result.amount)}`,
        message: result.created === false ? message : `${split}.`,
      });
      setDone(result);
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const chip = (active: boolean) =>
    `min-h-11 rounded-xl border px-3 text-sm font-medium transition-colors ${
      active ? "border-black bg-[var(--color-primary-light)] text-black" : "border-gray-200 text-gray-600 hover:border-gray-400"
    }`;

  return (
    <Modal open={open} onClose={onClose} title="Pay Back To Customer" maxWidth="max-w-md">
      {done ? (
        <div className="space-y-4" data-testid="payback-result">
          <div className="flex items-start gap-3 rounded-xl border border-[var(--color-card-border)] px-3.5 py-3">
            <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--ui-success-bg,#E7F6EC)] text-[var(--color-success)]">
              <Check className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <p className="font-mono-num text-xl font-bold text-black">{rupees(done.amount)}</p>
              <p className="text-sm font-semibold text-black">{paybackSplitLine(done.wallet_amount, done.goodwill_amount)}</p>
              <p className="mt-0.5 text-xs text-gray-500">
                {[done.booking_number, done.reason_label || (done.reason ? PAYBACK_REASON_LABELS[done.reason as PaybackReason] : ""), done.method ? PAYOUT_METHOD_LABELS[done.method as PayoutMethod] || toTitle(done.method) : ""]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
              <p className="mt-0.5 text-xs text-gray-500">Wallet now {done.balance < 0 ? "−" : ""}{rupees(done.balance)}. The customer is told on WhatsApp.</p>
            </div>
          </div>
          <Button className="min-h-11 w-full" onClick={onClose}>
            Done
          </Button>
        </div>
      ) : (
        <div className="space-y-4">
          <p className="rounded-xl bg-[var(--color-primary-light)] px-3 py-2.5 text-sm text-[#0E1A33]">
            Only for a booking that was cancelled, delayed or had an issue. Pay {customerName || "the customer"} from your side first, then record it here.
          </p>

          {/* 1 — Which booking */}
          <fieldset>
            <legend className="mb-1.5 text-sm font-medium text-[#0E1A33]">Booking</legend>
            {loadingBookings ? (
              <p className="text-sm text-gray-500">Loading their bookings…</p>
            ) : !bookings.length ? (
              <p className="rounded-xl border border-dashed border-gray-300 px-3 py-3 text-sm text-gray-500">
                No cancelled, delayed or flagged booking for this customer — a payback needs one.
              </p>
            ) : (
              <div className="max-h-56 space-y-2 overflow-y-auto" role="radiogroup" aria-label="Booking">
                {bookings.map((b) => (
                  <button
                    key={b.id}
                    type="button"
                    role="radio"
                    aria-checked={bookingId === b.id}
                    onClick={() => {
                      setBookingId(b.id);
                      if (b.signals[0]) setReason(b.signals[0]);
                    }}
                    className={`flex min-h-11 w-full items-start justify-between gap-3 rounded-xl border px-3 py-2 text-left ${
                      bookingId === b.id ? "border-black bg-[var(--color-primary-light)]" : "border-gray-200 hover:border-gray-400"
                    }`}
                    data-testid="payback-booking"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium text-black">{b.title}</span>
                      <span className="block text-xs text-gray-500">
                        <span className="font-mono-num">{b.booking_number}</span>
                        {b.when ? ` · ${b.when}` : ""}
                      </span>
                      {b.signals.length > 0 && <span className="block text-xs font-semibold text-black">{b.signals.map((s) => PAYBACK_REASON_LABELS[s]).join(" · ")}</span>}
                    </span>
                    <span className="shrink-0 text-right text-xs text-gray-500">
                      {b.paid > 0 && <span className="block font-mono-num">Paid {rupees(b.paid)}</span>}
                      {b.paidBack > 0 && <span className="block font-mono-num">Back {rupees(b.paidBack)}</span>}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </fieldset>

          {/* 2 — Why */}
          <fieldset>
            <legend className="mb-1.5 text-sm font-medium text-[#0E1A33]">Reason</legend>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3" role="radiogroup" aria-label="Reason">
              {REASONS.map((r) => (
                <button key={r} type="button" role="radio" aria-checked={reason === r} onClick={() => setReason(r)} className={chip(reason === r)}>
                  {PAYBACK_REASON_LABELS[r]}
                </button>
              ))}
            </div>
          </fieldset>

          {/* 3 — How you paid */}
          <fieldset>
            <legend className="mb-1.5 text-sm font-medium text-[#0E1A33]">Paid By You In</legend>
            <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label="Method">
              {METHODS.map((m) => (
                <button key={m} type="button" role="radio" aria-checked={method === m} onClick={() => setMethod(m)} className={chip(method === m)}>
                  {PAYOUT_METHOD_LABELS[m]}
                </button>
              ))}
            </div>
          </fieldset>
          {method !== "cash" && (
            <Input
              label="Transfer Reference"
              maxLength={100}
              value={reference}
              onChange={(e) => setReference(e.target.value)}
              placeholder={method === "upi" ? "UPI ref / UTR no." : "Bank ref / UTR no."}
              error={reference.trim() !== "" && !refOk ? "At least 3 characters." : undefined}
            />
          )}

          <Input
            label="Amount (₹)"
            inputMode="numeric"
            value={amount}
            onChange={(e) => setAmount(e.target.value.replace(/\D/g, "").slice(0, 7))}
            placeholder="0"
            hint={picked && left > 0 ? `Up to ${rupees(left)} — what they paid for this booking.` : undefined}
          />
          {amountOk && (
            <div className="rounded-xl border border-[var(--color-card-border)] px-3 py-2.5 text-sm" data-testid="payback-split">
              <p className="font-semibold text-black">{paybackSplitLine(fromWallet, byYou)}</p>
              <p className="text-xs text-gray-500">
                {credit > 0 ? `Their wallet credit (${rupees(credit)}) is used first.` : "No wallet credit — all of it is paid by you."}
              </p>
              {cancelledShort && (
                <p className="mt-1 text-xs font-semibold text-[var(--color-error)]">
                  A cancelled booking&apos;s money is already in their wallet — you can pay back at most {rupees(credit)}.
                </p>
              )}
            </div>
          )}

          <Input
            label={reason === "other" ? "What Went Wrong" : "Note (Optional)"}
            maxLength={300}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={reason === "other" ? "At least 10 characters" : "e.g. Captain came 2 hours late"}
            error={reason === "other" && note.trim() !== "" && !noteOk ? "At least 10 characters." : undefined}
          />

          {error && (
            <p className="text-sm text-[var(--color-error)]" role="alert">
              {error}
            </p>
          )}
          <div className="flex gap-2">
            <Button variant="outline" className="min-h-11 flex-1" onClick={onClose}>
              Back
            </Button>
            <Button
              className="min-h-11 flex-1"
              isLoading={save.isPending}
              disabled={!bookingId || !amountOk || !refOk || !noteOk}
              onClick={() => {
                setError("");
                save.mutate();
              }}
            >
              <HandCoins className="hidden h-4 w-4 sm:inline" /> Record Payback
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
