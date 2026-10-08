import { apiClient, type ApiPaginated, type ApiSuccess } from "../lib/api-client";

/**
 * Late-cancellation charges (founder rule 2026-10-07). The charge is a
 * RECORD; the money is settled through the customer's wallet ("settled":
 * debited there, or netted against what they had paid). Managers (own
 * center) and admins can reduce or waive it — never raise it — and the
 * reduction is credited back to the wallet (or lowers the unpaid booking
 * now carrying it). Legacy states: "open" (on account, moved to the wallet
 * at boot) and "applied" (added to a booking's total).
 */
export type CancellationTier = "free" | "1_to_4h" | "under_1h" | "after_captain_left";
export type ChargeStatus = "open" | "applied" | "waived" | "settled";

export interface ChargeHistoryEntry {
  at: string;
  action: "created" | "applied" | "released" | "reduced" | "waived" | "moved_to_wallet" | string;
  by?: string | null;
  by_name?: string | null;
  role?: string | null;
  from?: number | null;
  to?: number | null;
  note?: string | null;
}

export interface CustomerCharge {
  id: string;
  customer_id: string;
  customer_name?: string | null;
  customer_phone?: string | null;
  service_center_id?: string | null;
  kind?: string | null;
  source_booking_id?: string | null;
  source_booking_number?: string | null;
  tier: CancellationTier;
  amount: number;
  original_amount: number;
  status: ChargeStatus;
  applied_to_booking_id?: string | null;
  applied_to_booking_number?: string | null;
  /** The booking carrying it is paid — it can't be reduced here any more. */
  applied_booking_paid?: boolean;
  created_at?: string | null;
  created_by_name?: string | null;
  created_by_role?: string | null;
  history: ChargeHistoryEntry[];
}

export interface MyCharges {
  open_total: number;
  items: {
    id: string;
    amount: number;
    tier: CancellationTier;
    source_booking_number?: string | null;
    created_at?: string | null;
    status: ChargeStatus;
    applied_to_booking_number?: string | null;
  }[];
}

/** What cancelling a booking (or its whole visit) would add to the
 *  customer's next booking right now. */
export interface CancellationChargePreview {
  tier: CancellationTier;
  amount: number;
  captain_left: boolean;
  minutes_to_slot: number;
  window_hours: number;
  /** false = one car of a visit that still goes ahead: always free. */
  ends_visit: boolean;
}

/** The result's charge summary on a cancel response. */
export interface LateCancellationCharge {
  id: string;
  amount: number;
  tier: CancellationTier;
  status: ChargeStatus;
}

export const TIER_LABELS: Record<CancellationTier, string> = {
  free: "More Than 4 Hours Before",
  "1_to_4h": "1–4 Hours Before The Slot",
  under_1h: "Less Than 1 Hour Before",
  after_captain_left: "After The Captain Left",
};

export const CHARGE_STATUS_LABELS: Record<ChargeStatus, string> = {
  open: "On Account",
  applied: "Added To Booking",
  waived: "Waived",
  settled: "Added To Wallet As Due",
};

export const chargeApi = {
  list: (params?: { status?: ChargeStatus; customer_id?: string; page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<CustomerCharge>>("/charges", { params }).then((r) => r.data),
  my: () => apiClient.get<ApiSuccess<MyCharges>>("/charges/my").then((r) => r.data.data),
  /** Reduce (amount below the current one) or waive (0). Never raises. */
  adjust: (id: string, amount: number, note?: string) =>
    apiClient.post<ApiSuccess<CustomerCharge>>(`/charges/${id}/adjust`, { amount, note: note || undefined }).then((r) => r.data.data),
};
