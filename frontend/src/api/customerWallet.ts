import { apiClient, type ApiPaginated, type ApiSuccess } from "../lib/api-client";

/**
 * Customer wallet (spec §1.1). One balance per customer, which can go
 * negative, and an immutable ledger. Credits: a cancelled paid booking (net
 * of its charge), a booking edited down, an overpayment, a reduced/waived
 * charge, admin adjustments. Debits: a late-cancel charge, balance spent on a
 * booking, a manager payback, admin adjustments. A negative balance is added to
 * the next booking ("Previous Balance Due"); a positive one is used on it.
 *
 * Shared by the customer app (`me`) and the staff screens (the rest).
 */

export type WalletEntryKind =
  | "cancellation"
  | "cancellation_charge"
  | "charge_migrated"
  | "charge_reduced"
  | "price_reduced"
  | "overpayment"
  | "booking_payment"
  | "previous_balance_paid"
  | "payout"
  | "adjustment";

export interface WalletEntry {
  id: string;
  /** Signed rupees: + credit, − debit. */
  amount: number;
  direction: "credit" | "debit";
  kind: WalletEntryKind | string;
  /** Plain-words reason from the server ("Booking cancelled", "Paid back to you"…). */
  label: string;
  balance_after: number;
  booking_id?: string | null;
  booking_number?: string | null;
  note?: string | null;
  actor_role?: string | null;
  actor_name?: string | null;
  /** Payouts: upi | bank_transfer | cash | other, and the transfer reference. */
  method?: string | null;
  reference?: string | null;
  /** A manager payback's reason (cancelled / delayed / …) and its words. */
  reason?: PaybackReason | string | null;
  reason_label?: string | null;
  /** Staff view: false = another center's booking (details hidden). */
  own_center?: boolean;
  /** Staff view: the booking number, or "Other Center". */
  booking_label?: string | null;
  created_at?: string | null;
}

export interface WalletSummary {
  customer_id: string;
  balance: number;
  /** Positive balance usable on the next booking. */
  credit_available: number;
  /** Debt the next booking will carry ("Previous Balance Due ₹X"). */
  previous_balance_due: number;
  /** Debt already riding on an unpaid booking. */
  carried_due: number;
}

export interface WalletView extends WalletSummary {
  items: WalletEntry[];
  total: number;
  page: number;
  page_size: number;
}

/** A ledger write's answer: the entry plus the new balance; `created`
 *  false = the same request was already applied (nothing changed). */
export type WalletPostResult = Partial<WalletEntry> & { created: boolean; balance: number };

export type PayoutMethod = "upi" | "bank_transfer" | "cash" | "other";

export const PAYOUT_METHOD_LABELS: Record<PayoutMethod, string> = {
  upi: "UPI",
  bank_transfer: "Bank Transfer",
  cash: "Cash",
  other: "Other",
};

/** Founder 2026-10-07 (MONEY-2): a manager never adds money at will — he
 *  only PAYS BACK for one booking that was cancelled, delayed or had an
 *  issue, from his own side, and records it here ("Paid By Manager"). */
export type PaybackReason = "cancelled" | "delayed" | "complaint" | "service_issue" | "other";

export const PAYBACK_REASON_LABELS: Record<PaybackReason, string> = {
  cancelled: "Cancelled",
  delayed: "Delayed",
  complaint: "Complaint",
  service_issue: "Service Issue",
  other: "Other",
};

export interface PaybackInput {
  booking_id: string;
  reason: PaybackReason;
  method: PayoutMethod;
  /** ≥ 3 characters; required unless cash. */
  reference?: string;
  amount: number;
  /** ≥ 10 characters when the reason is other. */
  note?: string;
  idempotency_key: string;
}

/** A recorded payback: the wallet credit used first, the rest paid by the
 *  manager (goodwill, on the booking only). */
export interface PaybackResult {
  id?: string;
  customer_id?: string;
  booking_id?: string;
  booking_number?: string | null;
  service_center_id?: string | null;
  amount: number;
  wallet_amount: number;
  goodwill_amount: number;
  mode: "wallet" | "goodwill" | "mixed";
  method?: string | null;
  reference?: string | null;
  reason?: string | null;
  reason_label?: string | null;
  note?: string | null;
  paid_by_id?: string | null;
  paid_by_name?: string | null;
  paid_by_role?: string | null;
  /** Wallet balance after. */
  balance: number;
  /** manager_payback | wallet_payout (older free payout) | razorpay_refund. */
  source?: string | null;
  created_at?: string | null;
  /** false = the same payback was already recorded (nothing changed). */
  created?: boolean;
}

/** GET /wallet/payouts rows (admin "Paybacks"). */
export interface WalletPayoutRow extends PaybackResult {
  id: string;
  customer_id: string;
  customer_name?: string | null;
  customer_phone?: string | null;
  center_name?: string | null;
}

/** Fresh key per dialog open — a double submit applies once. */
export function newIdempotencyKey(prefix = "w"): string {
  const id =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  return `${prefix}-${id}`.slice(0, 64);
}

export const customerWalletApi = {
  /** Customer: own balance + ledger. */
  me: (params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiSuccess<WalletView>>("/wallet/me", { params }).then((r) => r.data.data),
  /** Manager (customer known to their center) / admin. */
  forCustomer: (customerId: string, params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiSuccess<WalletView>>(`/customers/${customerId}/wallet`, { params }).then((r) => r.data.data),
  /** Pay Back To Customer (MONEY-2): money the manager paid from his side
   *  for ONE booking that was cancelled, delayed or had an issue. 400
   *  PAYBACK_NOT_ALLOWED / PAYBACK_TOO_MUCH (message has the limit) /
   *  WALLET_BALANCE_TOO_LOW. Idempotent on `idempotency_key`. */
  payback: (customerId: string, payload: PaybackInput) =>
    apiClient
      .post<ApiSuccess<PaybackResult>>(`/customers/${customerId}/wallet/payout`, {
        ...payload,
        reference: payload.reference?.trim() || undefined,
        note: payload.note?.trim() || undefined,
      })
      .then((r) => ({ result: r.data.data, message: r.data.message })),
  /** Admin correction: + credit, − debit. */
  adjust: (customerId: string, payload: { amount: number; note: string; idempotency_key: string }) =>
    apiClient
      .post<ApiSuccess<WalletPostResult>>(`/customers/${customerId}/wallet/adjust`, payload)
      .then((r) => ({ entry: r.data.data, message: r.data.message })),
  /** Admin: every payback (and older payouts), newest first. */
  payouts: (params?: { page?: number; page_size?: number; service_center_id?: string; date_from?: string; date_to?: string }) =>
    apiClient.get<ApiPaginated<WalletPayoutRow>>("/wallet/payouts", { params }).then((r) => r.data),
};
