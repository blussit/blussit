import { apiClient, type ApiPaginated, type ApiSuccess } from "../lib/api-client";
import type { PassExtension } from "../types";

/**
 * Custom multi-car plans — a manager's cart for ONE customer
 * (docs/FEATURE_PLAN_WALLET_EDITS_PLANS_2026-10-07.md §1.6). Per car: the
 * car and per-service wash counts. Every price is computed on the server
 * (POST /preview); the client never sends a total. One link or cash for
 * the whole cart; paying it starts one 30-day pass per car.
 *
 * Also here: the general pass extension (any pass, not only society) —
 * POST /subscriptions/{id}/extend.
 */

export type CustomPlanStatus = "draft" | "awaiting_payment" | "activating" | "active" | "needs_review" | "refunded" | "cancelled";

export interface CustomPlanItemInput {
  service_id: string;
  count: number;
}

/** A saved car (`vehicle_id`) OR a plate + car type. */
export interface CustomPlanCarInput {
  vehicle_id?: string;
  registration_number?: string;
  vehicle_type?: string;
  items: CustomPlanItemInput[];
}

/** The customer as create names them: `customer_id`, or phone (+ name). */
export interface CustomPlanCartInput {
  customer_id?: string;
  customer_phone?: string;
  customer_name?: string;
  cars: CustomPlanCarInput[];
  discount_amount: number;
}

/** Preview of a renewal: `renewal_of` = the paid cart being renewed, or the
 *  draft renewal being revised — its customer is used, and the cars' own
 *  renewed passes don't block them (any other live pass still does). */
export interface CustomPlanPreviewInput extends CustomPlanCartInput {
  renewal_of?: string;
}

export interface CustomPlanCreateInput extends CustomPlanCartInput {
  /** Admin only. */
  service_center_id?: string;
  note?: string;
}

export interface CustomPlanReviseInput {
  expected_revision: number;
  cars: CustomPlanCarInput[];
  discount_amount: number;
  note?: string;
}

export interface CustomPlanPricedItem {
  service_id: string;
  service_name?: string | null;
  count: number;
  unit_price: number;
  line_total: number;
  /** Cart views: washes of this service still left on the car's pass. */
  remaining?: number;
}

export interface CustomPlanPricedCar {
  vehicle_id?: string | null;
  registration_number?: string | null;
  vehicle_type?: string | null;
  vehicle_type_name?: string | null;
  items: CustomPlanPricedItem[];
  washes: number;
  price: number;
  discount_share?: number;
  amount?: number;
}

export interface CustomPlanPreview {
  cars: CustomPlanPricedCar[];
  subtotal: number;
  discount_amount: number;
  total_amount: number;
  period_days: number;
}

/** The pass a paid car got (staff views add can_extend / extension_days_left). */
export interface CustomPlanCarPass {
  id: string;
  status?: string | null;
  remaining: number;
  total: number;
  remaining_by_service: Record<string, number>;
  start_date?: string | null;
  end_date?: string | null;
  extension_days?: number | null;
  extended_until?: string | null;
  last_bookable_day?: string | null;
  in_extension?: boolean;
  /** "18 Oct 2026" — shown as "Last Booking Day: 18 Oct 2026". */
  last_booking_day_label?: string | null;
  /** A renewal lined up after this pass (its own cart's pass). */
  renewed_by_subscription_id?: string | null;
  can_extend?: boolean;
  extension_days_left?: number | null;
}

/** One car's refund (staff views add by / by_role / max_amount). */
export interface CustomPlanCarRefund {
  amount: number;
  at?: string | null;
  reason?: string | null;
  washes?: number | null;
  by?: string | null;
  by_role?: string | null;
  max_amount?: number | null;
}

export interface CustomPlanCar extends CustomPlanPricedCar {
  /** pending (unpaid) | active | skipped (already had another plan when
   *  paid) | refunded (its unused washes went to the customer's wallet). */
  status?: "pending" | "active" | "skipped" | "refunded" | string | null;
  note?: string | null;
  /** Staff only: this car's share to refund (a skipped car). */
  refund_due?: number | null;
  refund?: CustomPlanCarRefund | null;
  /** Staff only: the most a refund of this car can be (null: nothing to refund). */
  refundable_amount?: number | null;
  /** Renewal cart: the old pass this car renews. */
  renews_subscription_id?: string | null;
  /** When this car's 30 days start (its pass, or — unpaid renewal — if paid now). */
  starts_on?: string | null;
  /** "18 Oct 2026". */
  starts_on_label?: string | null;
  subscription_id?: string | null;
  subscription?: CustomPlanCarPass | null;
}

export interface CustomPlan {
  id: string;
  customer_id: string;
  customer_name?: string | null;
  customer_phone?: string | null;
  service_center_id?: string | null;
  plan_name?: string | null;
  status: CustomPlanStatus;
  revision: number;
  cars: CustomPlanCar[];
  car_count: number;
  washes: number;
  subtotal: number;
  discount_amount: number;
  total_amount: number;
  period_days: number;
  period_start?: string | null;
  period_end?: string | null;
  note?: string | null;
  payment?: { method?: "cash" | "online" | string | null; amount?: number | null; order_id?: string | null; at?: string | null } | null;
  payment_link?: { short_url?: string | null; amount: number; order_id: string } | null;
  created_at?: string | null;
  activated_at?: string | null;
  cancelled_at?: string | null;
  created_by?: string | null;
  created_by_role?: string | null;
  /** needs_review: why, which plates were skipped, and the refund owed. */
  review?: { reason?: string | null; skipped?: (string | null)[]; refund_due?: number | null } | null;
  cancel_reason?: string | null;
  /** This cart renews `renewal_of`; an old cart names its renewal. */
  renewal_of?: string | null;
  renewal_cart_id?: string | null;
  renewal_starts_on?: string | null;
  /** "18 Oct 2026". */
  renewal_starts_on_label?: string | null;
  /** The customer's button while unpaid ("Pay ₹1499 To Renew"). */
  pay_label?: string | null;
  refunded_amount?: number | null;
}

/** POST /renew — a new draft cart (revision 1) renewing a paid one. `cars`
 *  omitted = the old cars that got a pass, same counts. */
export interface CustomPlanRenewInput {
  cars?: CustomPlanCarInput[];
  discount_amount?: number;
  note?: string;
}

export interface CustomPlanRefundResult {
  /** true = this car was refunded before; nothing new credited. */
  already: boolean;
  refund?: CustomPlanCarRefund | null;
  wallet?: { balance?: number | null } | null;
  vehicle_id?: string | null;
  registration_number?: string | null;
  custom_plan: CustomPlan;
}

export interface CustomPlanLinkResult {
  short_url?: string | null;
  amount: number;
  order_id: string;
  reused: boolean;
  sent: boolean;
  custom_plan: CustomPlan;
}

export const CUSTOM_PLAN_STATUS_LABELS: Record<CustomPlanStatus, string> = {
  draft: "Not Sent Yet",
  awaiting_payment: "Awaiting Payment",
  activating: "Activating",
  active: "Active",
  needs_review: "Needs Review",
  refunded: "Refunded",
  cancelled: "Cancelled",
};

export const customPlanApi = {
  preview: (payload: CustomPlanPreviewInput) =>
    apiClient.post<ApiSuccess<CustomPlanPreview>>("/subscriptions/custom-plans/preview", payload).then((r) => r.data.data),
  create: (payload: CustomPlanCreateInput) =>
    apiClient.post<ApiSuccess<CustomPlan>>("/subscriptions/custom-plans", payload).then((r) => r.data.data),
  list: (params?: { status?: CustomPlanStatus; customer_id?: string; service_center_id?: string; page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<CustomPlan>>("/subscriptions/custom-plans", { params }).then((r) => r.data),
  get: (id: string) => apiClient.get<ApiSuccess<CustomPlan>>(`/subscriptions/custom-plans/${id}`).then((r) => r.data.data),
  /** 409 when the cart changed since `expected_revision`. */
  revise: (id: string, payload: CustomPlanReviseInput) =>
    apiClient.put<ApiSuccess<CustomPlan>>(`/subscriptions/custom-plans/${id}`, payload).then((r) => r.data.data),
  sendLink: (id: string, expectedRevision: number, sendWhatsapp: boolean) =>
    apiClient
      .post<ApiSuccess<CustomPlanLinkResult>>(`/subscriptions/custom-plans/${id}/link`, { expected_revision: expectedRevision, send_whatsapp: sendWhatsapp })
      .then((r) => r.data.data),
  /** Returns the cart; `message` says which cars were skipped (if any). */
  markCash: (id: string, expectedRevision: number, note?: string) =>
    apiClient
      .post<ApiSuccess<CustomPlan>>(`/subscriptions/custom-plans/${id}/cash`, { expected_revision: expectedRevision, note: note || undefined })
      .then((r) => ({ plan: r.data.data, message: r.data.message })),
  cancel: (id: string, reason?: string) =>
    apiClient.post<ApiSuccess<CustomPlan>>(`/subscriptions/custom-plans/${id}/cancel`, { reason: reason || undefined }).then((r) => r.data.data),
  /** A paid cart → a NEW draft cart (revision 1); then link / cash it with
   *  expected_revision 1. 400s carry a plain message. */
  renew: (id: string, payload: CustomPlanRenewInput) =>
    apiClient.post<ApiSuccess<CustomPlan>>(`/subscriptions/custom-plans/${id}/renew`, payload).then((r) => r.data.data),
  /** Refund ONE car (`carRef`: its index or vehicle_id) to the customer's
   *  wallet — at most its refundable_amount (omitted = that max). */
  refundCar: (id: string, carRef: string | number, payload: { amount?: number; reason: string }) =>
    apiClient
      .post<ApiSuccess<CustomPlanRefundResult>>(`/subscriptions/custom-plans/${id}/cars/${encodeURIComponent(String(carRef))}/refund`, payload)
      .then((r) => ({ result: r.data.data, message: r.data.message })),
};

/** What an extended pass reads like afterwards (only the fields we show). */
export interface ExtendedPass {
  id?: string;
  extension_days?: number;
  extension_days_left?: number;
  extended_until?: string | null;
  usable_until?: string | null;
  last_bookable_day?: string | null;
  last_booking_day_label?: string | null;
  extensions?: PassExtension[];
}

export const passApi = {
  /** Manager (the pass's own center) / admin: 1–10 more days, at most 10
   *  per 30-day period, in the last 3 days or after the end. */
  extend: (subscriptionId: string, days: number, note?: string) =>
    apiClient
      .post<ApiSuccess<ExtendedPass>>(`/subscriptions/${subscriptionId}/extend`, { days, note: note || undefined })
      .then((r) => r.data.data),
};

/** Extension fields staff pass views carry (center/admin overview rows,
 *  customer-360 passes) beyond the shared UserSubscription type. */
export interface StaffPassExtras {
  can_extend?: boolean;
  extension_days?: number;
  extension_days_left?: number | null;
  extended_until?: string | null;
  usable_until?: string | null;
  last_bookable_day?: string | null;
  last_booking_day_label?: string | null;
  in_extension?: boolean;
  extensions?: PassExtension[];
  society_id?: string | null;
  vehicle_id?: string | null;
  registration_number?: string | null;
  plan_kind?: string | null;
  custom_plan_id?: string | null;
  total_by_service?: Record<string, number> | null;
  remaining_by_service?: Record<string, number> | null;
}
