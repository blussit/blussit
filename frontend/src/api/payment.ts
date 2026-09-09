import { apiClient, type ApiSuccess } from "../lib/api-client";
import type { UserSubscription } from "../types";

/** Server-minted Razorpay order — the amount is resolved backend-side
 * from the booking/plan, never taken from the browser. key_id is the
 * PUBLIC half of the credentials (the modal needs it); the secret never
 * leaves the backend. */
export interface RazorpayOrder {
  order_id: string;
  amount: number; // paise
  currency: string;
  key_id: string;
  description: string;
}

export interface VerifyPaymentResult {
  status: "paid";
  purpose: "booking" | "subscription";
  booking_id?: string;
  subscription?: UserSubscription & { plan_name?: string };
  confirmation_token?: string;
  already_processed?: boolean;
}

export const paymentApi = {
  createOrder: (payload: { purpose: "booking" | "subscription"; booking_id?: string; plan_id?: string; vehicle_type?: string }) =>
    apiClient.post<ApiSuccess<RazorpayOrder>>("/payments/create-order", payload).then((r) => r.data.data),
  verify: (payload: { razorpay_order_id: string; razorpay_payment_id: string; razorpay_signature: string }) =>
    apiClient.post<ApiSuccess<VerifyPaymentResult>>("/payments/verify", payload).then((r) => r.data.data),
  // Captain doorstep settlement (see CollectPaymentModal).
  collectCash: (bookingId: string) =>
    apiClient.post<ApiSuccess<{ payment_status: string; payment_method: string }>>("/payments/collect/cash", { booking_id: bookingId }).then((r) => r.data.data),
  collectLink: (bookingId: string) =>
    apiClient.post<ApiSuccess<{ short_url: string; amount: number }>>("/payments/collect/link", { booking_id: bookingId }).then((r) => r.data.data),
  collectStatus: (bookingId: string) =>
    apiClient.get<ApiSuccess<{ payment_status: string; payment_method?: string }>>(`/payments/collect/status/${bookingId}`).then((r) => r.data.data),
  // Collections reporting — manager (per captain) and admin (per center).
  centerCollections: (centerId: string, params?: { date_from?: string; date_to?: string }) =>
    apiClient.get<ApiSuccess<CollectionsReport>>(`/payments/collections/center/${centerId}`, { params }).then((r) => r.data.data),
  adminCollections: (params?: { date_from?: string; date_to?: string }) =>
    apiClient.get<ApiSuccess<CollectionsReport>>("/payments/collections/admin", { params }).then((r) => r.data.data),
};

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
  uncollected_amount: number;
  uncollected_count: number;
}

export interface CollectionsReport {
  rows: CollectionsRow[];
  totals: Omit<CollectionsRow, "captain_id" | "captain_name" | "employee_id" | "service_center_id" | "center_name">;
  /** Admin roll-up only. */
  subscriptions?: { online_amount: number; count: number };
  attention?: { reason?: string | null; booking_number?: string | null; purpose?: string | null; amount: number; flagged_at?: string | null }[];
}
