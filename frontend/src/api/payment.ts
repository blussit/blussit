import { apiClient, type ApiSuccess } from "../lib/api-client";
import type { UserSubscription } from "../types";

/** Server-minted Razorpay order — the amount is resolved backend-side
 * from the booking/plan, never taken from the browser. key_id is the
 * PUBLIC half of the credentials (the modal needs it); the secret never
 * leaves the backend. */
export interface RazorpayOrder {
  /** One-time payment. Exactly one of order_id / subscription_id is set. */
  order_id?: string;
  /** Auto-pay: a recurring mandate for checkout to authorise instead. */
  subscription_id?: string;
  auto_pay?: boolean;
  /** Auto-pay was asked for but the gateway couldn't set a mandate up —
   *  this is a plain one-cycle purchase and the UI must say so. */
  auto_pay_unavailable?: boolean;
  amount: number; // paise
  currency: string;
  key_id: string;
  description: string;
  /** "test" | "live" — read off the key id server-side. A test payment that
   *  looks identical to a real one is how fake revenue gets reported. */
  mode?: "test" | "live" | "unconfigured";
}

export interface VerifyPaymentResult {
  status: "paid";
  purpose: "booking" | "booking_group" | "subscription" | "society";
  /** The purchase set up a recurring mandate, not just one cycle. */
  auto_pay?: boolean;
  booking_id?: string;
  booking_group_id?: string;
  subscription_id?: string;
  subscription?: UserSubscription & { plan_name?: string };
  /** Issued once per purchase, to whichever request confirms it first. */
  confirmation_token?: string;
  already_processed?: boolean;
}

/** An order's state as the server (asking Razorpay) sees it right now. */
export interface PaymentStatusResult {
  status: "paid" | "pending" | "failed" | "needs_attention";
  purpose: "booking" | "booking_group" | "subscription" | "society";
  confirming?: boolean;
  failure_reason?: string | null;
  booking_id?: string;
  booking_group_id?: string;
  subscription_id?: string;
  subscription?: UserSubscription & { plan_name?: string };
  confirmation_token?: string;
  auto_pay?: boolean;
}

/** What the customer's booking page says about its payment. */
export interface BookingPaymentState {
  booking_id: string;
  payment_status: string;
  /** The newest attempt failed and can still be retried. */
  last_failure: { reason: string; at?: string | null } | null;
  /** Money received that couldn't be applied — being fixed or refunded. */
  attention: { amount: number; message: string; at?: string | null } | null;
  confirming: boolean;
}

export interface PaymentFailureReport {
  razorpay_order_id?: string;
  razorpay_subscription_id?: string;
  razorpay_payment_id?: string;
  code?: string;
  description?: string;
  reason?: string;
  step?: string;
  source?: string;
}

export const paymentApi = {
  createOrder: (payload: { purpose: "booking" | "booking_group" | "subscription" | "society"; booking_id?: string; booking_group_id?: string; plan_id?: string; vehicle_id?: string; service_id?: string; vehicle_type?: string; auto_pay?: boolean; society_enrollment_id?: string; society_renewal?: boolean; society_coupon_code?: string }) =>
    apiClient.post<ApiSuccess<RazorpayOrder>>("/payments/create-order", payload).then((r) => r.data.data),
  verify: (payload: { razorpay_order_id?: string; razorpay_subscription_id?: string; razorpay_payment_id: string; razorpay_signature: string }) =>
    apiClient.post<ApiSuccess<VerifyPaymentResult>>("/payments/verify", payload).then((r) => r.data.data),
  reportFailure: (payload: PaymentFailureReport) =>
    apiClient.post<ApiSuccess<{ recorded: boolean }>>("/payments/failure", payload).then((r) => r.data.data),
  status: (params: { order_id?: string; subscription_id?: string }) =>
    apiClient.get<ApiSuccess<PaymentStatusResult>>("/payments/status", { params }).then((r) => r.data.data),
  bookingState: (bookingId: string) =>
    apiClient.get<ApiSuccess<BookingPaymentState>>(`/payments/bookings/${bookingId}/state`).then((r) => r.data.data),
  // Admin: a parked payment was refunded/activated — take it off the queue.
  resolveAttention: (id: string, note: string) =>
    apiClient.post<ApiSuccess<{ id: string; resolved: boolean }>>(`/payments/attention/${encodeURIComponent(id)}/resolve`, { note }).then((r) => r.data.data),
  // Captain doorstep settlement (see CollectPaymentModal).
  // A booking on a multi-car visit settles the VISIT: amount and vehicles
  // cover every car on it, and "paid" means every car is paid.
  collectCash: (bookingId: string) =>
    apiClient.post<ApiSuccess<CollectResult>>("/payments/collect/cash", { booking_id: bookingId }).then((r) => r.data.data),
  collectLink: (bookingId: string) =>
    apiClient.post<ApiSuccess<{ short_url: string; amount: number }>>("/payments/collect/link", { booking_id: bookingId }).then((r) => r.data.data),
  collectStatus: (bookingId: string) =>
    apiClient.get<ApiSuccess<CollectResult>>(`/payments/collect/status/${bookingId}`).then((r) => r.data.data),
  // Collections reporting — manager (per captain) and admin (per center).
  centerCollections: (centerId: string, params?: { date_from?: string; date_to?: string }) =>
    apiClient.get<ApiSuccess<CollectionsReport>>(`/payments/collections/center/${centerId}`, { params }).then((r) => r.data.data),
  adminCollections: (params?: { date_from?: string; date_to?: string }) =>
    apiClient.get<ApiSuccess<CollectionsReport>>("/payments/collections/admin", { params }).then((r) => r.data.data),
};

export interface CollectResult {
  payment_status: string;
  payment_method?: string;
  /** Rupees still owed (status) or just collected (cash). */
  amount?: number;
  vehicles?: number;
}

export interface CollectionsRow {
  captain_id?: string | null;
  captain_name?: string | null;
  employee_id?: string | null;
  service_center_id?: string | null;
  center_name?: string | null;
  cash_amount: number;
  cash_count: number;
  online_amount: number;
  online_count: number;
  /** Part of online_amount recorded by a manager (UPI) — not through Razorpay. */
  manual_online_amount?: number;
  uncollected_amount: number;
  uncollected_count: number;
  /** Completed washes in range — separate from the money fields since a
   *  plan-covered wash is usually ₹0 (paid up front when the plan itself
   *  was bought). */
  washes_count: number;
  /** Of washes_count, how many drew on a subscription/plan. */
  plan_washes_count: number;
}

export interface CollectionsReport {
  rows: CollectionsRow[];
  totals: Omit<CollectionsRow, "captain_id" | "captain_name" | "employee_id" | "service_center_id" | "center_name">;
  /** Admin roll-up only. */
  subscriptions?: { online_amount: number; cash_amount: number; count: number; cash_count: number };
  attention?: {
    id?: string;
    reason?: string | null;
    booking_number?: string | null;
    purpose?: string | null;
    kind?: string | null;
    amount: number;
    customer_id?: string | null;
    /** Razorpay payment id to refund/look up. */
    payment_id?: string | null;
    gateway_ref?: string | null;
    flagged_at?: string | null;
    /** What a parked PLAN payment was for. */
    plan_name?: string | null;
    vehicle_type_name?: string | null;
    service_name?: string | null;
  }[];
}
