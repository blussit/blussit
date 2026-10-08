import { apiClient, type ApiSuccess } from "../lib/api-client";
import type { Booking } from "../types";
import type { LateCancellationCharge } from "./charges";

/**
 * Staff-side booking calls and fields added with the wallet / edits /
 * on-site add-ons backend (spec §1.2–1.5). Kept apart from api/booking.ts
 * (the customer app's module).
 */

/** One service added on site (captain or manager), after the booking was made. */
export interface AddedService {
  service_id: string;
  name?: string | null;
  qty: number;
  unit_price?: number;
  amount: number;
  is_addon?: boolean;
  by?: string | null;
  by_name?: string | null;
  role?: string | null;
  at?: string | null;
  /** The booking's status when it was added (e.g. "completed" = after the wash). */
  stage?: string | null;
  note?: string | null;
}

export interface HistoryChange {
  booking_id?: string;
  booking_number?: string | null;
  field: string;
  from?: unknown;
  to?: unknown;
}

export interface StatusHistoryRow {
  status: string;
  note?: string | null;
  created_at: string;
  changed_by?: string | null;
  changes?: HistoryChange[] | null;
}

/** One manager payback recorded on the booking (MONEY-2). */
export interface ManagerPayback {
  id?: string;
  amount: number;
  wallet_amount?: number | null;
  goodwill_amount?: number | null;
  method?: string | null;
  reference?: string | null;
  reason?: string | null;
  /** Not stored on the booking today — derive from `reason` when missing. */
  reason_label?: string | null;
  note?: string | null;
  by?: string | null;
  by_name?: string | null;
  by_role?: string | null;
  at?: string | null;
}

export type TipMethod = "cash" | "online";

/** Money + edit fields every booking now carries. */
export interface BookingMoneyFields {
  total_amount?: number;
  wallet_applied?: number | null;
  amount_paid?: number | null;
  amount_due?: number | null;
  /** A negative wallet balance added to this booking ("Previous Balance Due"). */
  wallet_due_carried?: number | null;
  paid_cash?: number | null;
  paid_online?: number | null;
  added_services?: AddedService[] | null;
  added_services_total?: number | null;
  customer_edited_at?: string | null;
  customer_edited_fields?: string[] | null;
  customer_edited_by_role?: string | null;
  status_history?: StatusHistoryRow[];
  /** How the tip itself was handed over (MONEY-2); a tip without it is cash. */
  tip_method?: TipMethod | null;
  /** Money a manager paid back for this booking, and the running total. */
  manager_paybacks?: ManagerPayback[] | null;
  paid_back_total?: number | null;
}

export type StaffBooking = Booking & BookingMoneyFields;

export interface AddServicesResult {
  booking: StaffBooking;
  added: AddedService[];
  added_total: number;
  amount_due: number;
  visit_amount_due: number;
}

/** What a cancel did to the customer's wallet. */
export interface CancelWalletResult {
  credited?: number | null;
  charge?: number | null;
  net?: number | null;
  balance?: number | null;
  wallet_line?: string | null;
}

export interface StaffCancelInput {
  reason: string;
  at_customer_request?: boolean;
  charge_amount?: number;
  /** Hand a plan wash back even inside its last hour (used up by default). */
  return_plan_wash?: boolean;
}

export interface StaffCancelResult {
  late_cancellation_charge?: LateCancellationCharge | null;
  wallet?: CancelWalletResult | null;
  /** Single cancel: bool. Visit cancel: how many plan washes were used up. */
  plan_wash_forfeited?: boolean | number | null;
}

export const staffBookingApi = {
  /** Manager (own center) / admin: any non-cancelled booking, even after
   *  completion. The added amount becomes due. */
  addServices: (bookingId: string, payload: { service_ids: string[]; quantities?: Record<string, number>; note?: string }) =>
    apiClient
      .post<ApiSuccess<AddServicesResult>>(`/bookings/${bookingId}/add-services`, { ...payload, note: payload.note || undefined })
      .then((r) => r.data.data),
  /** Fresh read with status_history (the list rows don't carry it). */
  get: (bookingId: string) => apiClient.get<ApiSuccess<StaffBooking>>(`/bookings/${bookingId}`).then((r) => r.data.data),
  cancel: (bookingId: string, input: StaffCancelInput) =>
    apiClient.post<ApiSuccess<StaffCancelResult>>(`/bookings/${bookingId}/cancel`, input).then((r) => r.data.data),
  /** Add / correct the tip on a manager-done job (0 removes it), with how
   *  the tip itself was handed over. */
  setTip: (bookingId: string, tipAmount: number, tipMethod: TipMethod) =>
    apiClient
      .patch<ApiSuccess<StaffBooking>>(`/bookings/${bookingId}/tip`, { tip_amount: tipAmount, tip_method: tipMethod })
      .then((r) => r.data.data),
  cancelGroup: (groupId: string, input: StaffCancelInput) =>
    apiClient.post<ApiSuccess<StaffCancelResult & { cancelled_count?: number }>>(`/bookings/group/${groupId}/cancel`, input).then((r) => r.data.data),
};

/** Money of one car, falling back for bookings made before partial payments. */
export function moneyOf(b: StaffBooking): { total: number; wallet: number; paid: number; due: number; carried: number } {
  const total = Number(b.total_amount || 0);
  const wallet = Number(b.wallet_applied || 0);
  const legacyPaid = b.payment_status === "paid" ? Math.max(0, total - wallet) : 0;
  const paid = b.amount_paid != null ? Number(b.amount_paid) : legacyPaid;
  const due = b.amount_due != null ? Number(b.amount_due) : Math.max(0, total - wallet - paid);
  return { total, wallet, paid, due, carried: Number(b.wallet_due_carried || 0) };
}

export const TIP_METHOD_LABELS: Record<TipMethod, string> = { cash: "Cash", online: "Online" };

export const EDITED_FIELD_LABELS: Record<string, string> = {
  scheduled_date: "Date",
  scheduled_slot: "Slot",
  slot: "Slot",
  date: "Date",
  address: "Address",
  address_id: "Address",
  service_ids: "Services",
  services: "Services",
  addon_ids: "Add-Ons",
  service_quantities: "Quantities",
  vehicle_type: "Car Type",
  vehicle_id: "Car",
  vehicle: "Car",
  notes: "Notes",
  special_instructions: "Notes",
  total: "Total",
  total_amount: "Total",
  added_services: "Added Services",
};
