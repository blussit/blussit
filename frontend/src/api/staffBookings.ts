import { apiClient, type ApiSuccess } from "../lib/api-client";
import type { PlanSplit } from "../lib/planSplit";
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
  /** A plan booking's bill: what the plan covers vs the paid add-ons. */
  plan_split?: PlanSplit | null;
  customer_edited_at?: string | null;
  customer_edited_fields?: string[] | null;
  customer_edited_by_role?: string | null;
  status_history?: StatusHistoryRow[];
  /** How the tip itself was handed over (MONEY-2); a tip without it is cash. */
  tip_method?: TipMethod | null;
  /** Money a manager paid back for this booking, and the running total. */
  manager_paybacks?: ManagerPayback[] | null;
  paid_back_total?: number | null;
  /** Staff reads only: changes whenever the booking's money, schedule or status does (the edit's `expected_version`). */
  version?: string;
  manager_discount_reason?: string | null;
  admin_notes?: string | null;
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

// -- Discounts and admin edits (founder 2026-10-28) -----------------------

export interface DiscountCar {
  booking_id: string;
  booking_number: string;
  discount_before: number;
  discount_after: number;
  total_before: number;
  total_after: number;
  amount_due_before: number;
  amount_due_after: number;
  amount_paid_after: number;
  payment_status: string;
  wallet_credit: number;
}

export interface DiscountResult {
  booking_id: string;
  booking_number: string;
  scope: "booking" | "visit";
  changed: boolean;
  dry_run: boolean;
  already_applied: boolean;
  discount_before: number;
  discount_after: number;
  total_after: number;
  amount_due_after: number;
  wallet_credit: number;
  cars: DiscountCar[];
  message: string;
  version: string;
}

export interface DiscountInput {
  amount?: number;
  percent?: number;
  reason: string;
  scope?: "booking" | "visit";
  idempotency_key?: string;
  expected_version?: string;
  dry_run?: boolean;
}

export interface MoneySnapshot {
  manager_discount: number;
  total: number;
  amount_paid: number;
  wallet_applied: number;
  amount_due: number;
  payment_status: string;
  platform_earning: number;
  tip_amount: number;
}

export interface AdjustmentChange {
  booking_id: string;
  booking_number?: string | null;
  field: string;
  label: string;
  from?: unknown;
  to?: unknown;
}

/** One immutable row of a booking's money-change history. */
export interface BookingAdjustment {
  id: string;
  booking_id: string;
  booking_number?: string | null;
  kind: "discount" | "admin_edit";
  seq: number;
  actor_id?: string | null;
  actor_role?: string | null;
  actor_name?: string | null;
  reason: string;
  before: MoneySnapshot;
  after: MoneySnapshot;
  wallet_credit?: number | null;
  changes?: AdjustmentChange[] | null;
  fields?: string[] | null;
  created_at: string;
}

export interface AdminEditResult {
  booking_id: string;
  booking_number: string;
  dry_run: boolean;
  already_applied: boolean;
  changes: AdjustmentChange[];
  warnings: string[];
  money: {
    before: MoneySnapshot;
    after: MoneySnapshot;
    customer_owes: number;
    wallet_credit: number;
    wallet_refund_on_cancel: number;
    captain_wallet_delta: number;
  };
  version: string;
  message: string;
}

export type AdminEditInput = Record<string, unknown> & { reason: string; dry_run?: boolean; expected_version?: string; idempotency_key?: string };

export const adjustmentApi = {
  discount: (bookingId: string, input: DiscountInput) =>
    apiClient.post<ApiSuccess<DiscountResult>>(`/bookings/${bookingId}/discount`, input).then((r) => r.data.data),
  /** The booking's money-change history, newest first (manager: own center; admin: any). */
  history: (bookingId: string) => apiClient.get<ApiSuccess<BookingAdjustment[]>>(`/bookings/${bookingId}/adjustments`).then((r) => r.data.data),
  adminEdit: (bookingId: string, input: AdminEditInput) =>
    apiClient.patch<ApiSuccess<AdminEditResult>>(`/admin/bookings/${bookingId}`, input).then((r) => r.data.data),
};

/** A fresh idempotency key for one dialog session (a retry reuses it). */
export function newIdempotencyKey(): string {
  const bytes = new Uint8Array(12);
  (globalThis.crypto || ({} as Crypto)).getRandomValues?.(bytes);
  return "k" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

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
