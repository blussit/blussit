/**
 * Customer-app money helpers (spec 2026-10-07 §1.1, §1.5) — display only.
 *
 * Every booking now reads: total_amount (incl. any previous balance carried
 * in), wallet_applied (wallet credit used), amount_paid, and
 * amount_due = max(0, total − wallet_applied − amount_paid). Older API
 * responses lack these fields, so each helper falls back to the old rule
 * (an unpaid car owes its whole total).
 */
import type { Booking, UserSubscription } from "../../types";
import type { CustomerWalletEntry } from "../../api/customerWalletMe";
import { toTitle } from "../../lib/titleCase";

/** The money fields a booking carries since 2026-10-07 (all optional). */
export type BookingMoney = Booking & {
  wallet_applied?: number | null;
  amount_paid?: number | null;
  amount_due?: number | null;
  wallet_due_carried?: number | null;
  added_services_total?: number | null;
  /** Services the captain / manager added at the visit (spec 1.4). */
  added_services?: AddedService[] | null;
};

export interface AddedService {
  service_id?: string;
  name?: string | null;
  qty?: number | null;
  amount?: number | null;
  by_name?: string | null;
  role?: string | null;
  at?: string | null;
}

/** "Added By Your Captain" / "Added By Your Manager". */
export const addedByLine = (a: AddedService) =>
  a.role === "captain" ? "Added By Your Captain" : a.role === "manager" ? "Added By Your Manager" : a.role === "admin" ? "Added By Blussit" : "Added On Site";

const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : 0);
export const rupees = (v: number) => `₹${Math.round(Math.abs(v))}`;

/** Does this response carry the new money view? */
export const hasMoneyView = (cars: Booking[]) => cars.some((c) => typeof (c as BookingMoney).amount_due === "number");

/** Wallet lines a booking quote (POST /bookings/quote) may carry. */
export interface WalletQuoteLines {
  previous_balance_due?: number;
  wallet_due_carried?: number;
  wallet_credit_available?: number;
  wallet_applied?: number;
  wallet_balance?: number;
  amount_payable?: number;
}

/** Wallet lines a booking create response may carry. */
export interface WalletResultLines {
  wallet_applied?: number;
  wallet_due_carried?: number;
  amount_due?: number;
}

/** What is still to pay on the not-cancelled cars of a visit. */
export function visitDue(cars: Booking[]): number {
  const live = cars.filter((c) => c.status !== "cancelled");
  if (hasMoneyView(cars)) return live.reduce((s, c) => s + num((c as BookingMoney).amount_due), 0);
  return live.filter((c) => c.payment_status !== "paid" && (c.total_amount ?? 0) > 0).reduce((s, c) => s + (c.total_amount ?? 0), 0);
}

export function visitMoney(cars: Booking[]) {
  const m = cars as BookingMoney[];
  return {
    walletApplied: m.reduce((s, c) => s + num(c.wallet_applied), 0),
    amountPaid: m.reduce((s, c) => s + num(c.amount_paid), 0),
    walletCarried: m.reduce((s, c) => s + num(c.wallet_due_carried), 0),
    addedServices: m.reduce((s, c) => s + num(c.added_services_total), 0),
  };
}

/** payment_status in the customer's words. */
export function paymentStatusLabel(status?: string | null): string {
  switch (status) {
    case "paid":
      return "Paid";
    case "partially_paid":
      return "Part Paid";
    case "pending":
      return "Not Paid Yet";
    case "refund_due":
      return "Refund Due";
    case "refunded":
      return "Refunded";
    case "failed":
      return "Payment Failed";
    default:
      return status ? toTitle(status) : "";
  }
}

/** A ledger row's reason in plain words (Title Case). */
export function walletEntryTitle(e: CustomerWalletEntry): string {
  const credit = e.amount >= 0;
  switch (e.kind) {
    case "cancellation":
      return credit ? "Refund From Cancelled Booking" : "Cancellation Charge";
    case "cancellation_charge":
      return "Cancellation Charge";
    case "charge_migrated":
      return "Earlier Cancellation Charge";
    case "charge_reduced":
      return "Cancellation Charge Reduced";
    case "price_reduced":
      return "Booking Price Reduced";
    case "overpayment":
      return "Extra Payment Kept As Credit";
    case "booking_payment":
      return credit ? "Returned From Cancelled Booking" : "Used For Booking";
    case "previous_balance_paid":
      return "Previous Balance Paid";
    case "payout":
      // A manager paid money back for a booking (cash / UPI / bank).
      return "Paid Back To You";
    case "refund":
      // A custom-plan car refunded to the wallet ("Custom plan refund — <plate>").
      return "Plan Refund";
    case "adjustment":
      return "Adjustment By Blussit";
    default:
      return toTitle(e.label) || "Wallet";
  }
}

const METHOD_LABELS: Record<string, string> = { upi: "UPI", bank_transfer: "Bank Transfer", cash: "Cash", other: "Other" };
export const payoutMethodLabel = (m?: string | null) => (m ? METHOD_LABELS[m] || toTitle(m) : "");

const PAYBACK_REASONS: Record<string, string> = {
  cancelled: "Booking Cancelled",
  delayed: "Service Delayed",
  complaint: "Complaint",
  service_issue: "Service Issue",
  other: "Other",
};

/** A payback row's detail: "Service Delayed · UPI · Ref 1234". */
export function paybackLine(e: CustomerWalletEntry): string {
  const reason = e.reason_label || (e.reason ? PAYBACK_REASONS[e.reason] || toTitle(e.reason) : "");
  return [reason ? toTitle(reason) : "", payoutMethodLabel(e.method), e.reference ? `Ref ${e.reference}` : ""].filter(Boolean).join(" · ");
}

// ---- custom plans -----------------------------------------------------------

export type { PassServiceQuota } from "../../types";

/** A /subscriptions/my row — the custom-pass fields now live on UserSubscription. */
export type PassRow = UserSubscription;

export { isCustomPlanPass as isCustomPass } from "../../lib/passState";

/** "Deep Cleaning 1 Of 2 Left" */
export const quotaLine = (name: string | null | undefined, remaining: number, total: number) =>
  `${toTitle(name) || "Wash"} ${remaining} Of ${total} Left`;
