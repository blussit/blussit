import { apiClient, type ApiSuccess } from "../lib/api-client";

/**
 * The signed-in customer's wallet (GET /wallet/me, spec 2026-10-07 §1.1).
 * The balance can go negative: a negative balance is added to the next
 * booking ("Previous Balance Due"), a positive one is used on it.
 */
export type CustomerWalletKind =
  | "cancellation"
  | "cancellation_charge"
  | "charge_migrated"
  | "charge_reduced"
  | "price_reduced"
  | "overpayment"
  | "booking_payment"
  | "previous_balance_paid"
  | "payout"
  | "refund"
  | "adjustment";

export interface CustomerWalletEntry {
  id: string;
  /** Signed: + credit, − debit. */
  amount: number;
  direction: "credit" | "debit";
  kind: CustomerWalletKind | string;
  /** The server's own short reason (sentence case). */
  label?: string | null;
  balance_after: number;
  booking_id?: string | null;
  booking_number?: string | null;
  note?: string | null;
  actor_role?: string | null;
  actor_name?: string | null;
  /** Payouts: how the money was sent and its reference. */
  method?: string | null;
  reference?: string | null;
  /** A manager payback's reason (cancelled / delayed / complaint / service_issue / other) and its words. */
  reason?: string | null;
  reason_label?: string | null;
  created_at?: string | null;
}

export interface CustomerWalletMe {
  customer_id: string;
  balance: number;
  /** max(0, balance) — used automatically on the next booking. */
  credit_available: number;
  /** What the next booking will carry ("Previous Balance Due ₹X"). */
  previous_balance_due: number;
  /** Debt already riding on an unpaid booking. */
  carried_due: number;
  items: CustomerWalletEntry[];
  total: number;
  page: number;
  page_size: number;
}

export const customerWalletMeApi = {
  me: (params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiSuccess<CustomerWalletMe>>("/wallet/me", { params }).then((r) => r.data.data),
};

export const MY_WALLET_QUERY_KEY = ["my-wallet"] as const;
