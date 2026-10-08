import { apiClient, type ApiSuccess } from "../lib/api-client";
import type { Booking } from "../types";
import type { CancellationChargePreview } from "./charges";

/**
 * The customer's own booking edit (spec 2026-10-07 §1.3) and the wallet
 * side of a cancel (§1.2). Additive to api/booking.ts — the older calls
 * there keep their shapes.
 */
export interface BookingEditAddress {
  line1: string;
  landmark?: string;
  city?: string;
  state?: string;
  pincode?: string;
  latitude?: number | null;
  longitude?: number | null;
}

export interface BookingCarEdit {
  booking_id: string;
  vehicle_type?: string;
  vehicle_id?: string;
  service_ids?: string[];
  service_quantities?: Record<string, number>;
}

export interface BookingEditPayload {
  /** Both or neither. */
  scheduled_date?: string;
  scheduled_slot?: string;
  /** A saved address OR a new pinned one — never both. */
  address_id?: string;
  address?: BookingEditAddress;
  customer_notes?: string;
  /** Single-booking endpoint only: this car's changes. */
  vehicle_type?: string;
  vehicle_id?: string;
  service_ids?: string[];
  service_quantities?: Record<string, number>;
  /** Visit endpoint: per-car changes. */
  cars?: BookingCarEdit[];
  /** The visit total the customer was shown — a higher one is refused (409 PRICE_CHANGED). */
  expected_total?: number;
  /** Preview only: validated and priced, nothing written (BookingEditPreview back). */
  dry_run?: boolean;
}

export interface BookingEditChange {
  booking_id: string;
  booking_number?: string | null;
  /** date | slot | address | notes | vehicle | services | total */
  field: string;
  from: string | number | null;
  to: string | number | null;
}

export interface BookingEditResult {
  booking_group_id?: string | null;
  bookings: Booking[];
  changes: BookingEditChange[];
  notices: string[];
  total_amount: number;
  amount_due: number;
  /** Credited to the wallet because a paid booking got cheaper. */
  wallet_credit: number;
}

/** A dry run answers the same numbers (plus per-car totals); `bookings` is absent. */
export type BookingEditPreview = Omit<BookingEditResult, "bookings"> & {
  dry_run?: boolean;
  bookings?: Booking[];
  /** The visit's distance charge after the edit (on its paying car — per car below). */
  travel_charge?: number;
  cars?: { booking_id: string; booking_number?: string | null; total_amount: number; amount_due: number; travel_charge?: number }[];
};

/** The preview's 2026-10-07 fields (on top of the tier fields). */
export type CancelPreview = CancellationChargePreview & {
  ends_visit?: boolean;
  can_cancel?: boolean;
  refusal?: string | null;
  plan_covered?: boolean;
  plan_wash_forfeited_count?: number;
  plan_wash_returned?: boolean;
  /** What was paid + wallet credit spent — comes back before the charge. */
  wallet_credit?: number;
  /** wallet_credit − charge; negative = a debit added to the next booking. */
  net?: number;
  wallet_balance_after?: number;
};

/** What a cancel response says about the wallet. */
export interface CancelWalletSummary {
  credited?: number | null;
  charge?: number | null;
  net?: number | null;
  balance?: number | null;
  wallet_line?: string | null;
}

export interface CancelOutcome {
  wallet?: CancelWalletSummary | null;
  /** Single cancel: boolean; visit cancel: how many plan washes were used up. */
  plan_wash_forfeited?: boolean | number | null;
}

export const bookingEditApi = {
  edit: (id: string, payload: BookingEditPayload) =>
    apiClient.patch<ApiSuccess<BookingEditResult>>(`/bookings/${id}`, payload).then((r) => r.data.data),
  editGroup: (groupId: string, payload: BookingEditPayload) =>
    apiClient.patch<ApiSuccess<BookingEditResult>>(`/bookings/group/${groupId}`, payload).then((r) => r.data.data),
};
