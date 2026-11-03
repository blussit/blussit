/**
 * A booking's itemised bill, exactly as the server builds it
 * (BookingService._bill_view): every row adds up to `total`, so no screen
 * works out a price of its own.
 */
export interface BillLine {
  service_id?: string | null;
  name?: string | null;
  qty: number;
  /** What was charged for this line (the first-wash price when it applied). */
  amount: number;
  /** The regular price, only when `amount` is lower (shown struck through). */
  regular_amount?: number | null;
  is_addon: boolean;
}

export interface Bill {
  lines: BillLine[];
  /** A catalogue price changed after booking: one explicit row, never a wrong bill. */
  adjustment: number;
  discount: { kind: "coupon" | "plan" | "discount"; code?: string | null; amount: number } | null;
  manager_discount: number;
  on_site: { name?: string | null; qty: number; amount: number }[];
  travel: { amount: number; km?: number | null } | null;
  carried: number;
  cancellation: number;
  tip: { amount: number; method: string } | null;
  other: number;
  total: number;
}
