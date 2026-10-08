import { apiClient, type ApiSuccess } from "../lib/api-client";

/**
 * The customer's custom multi-car plans (GET /subscriptions/custom-plans/my,
 * spec 2026-10-07 §1.6): a manager-built cart, one car-bound pass per car
 * with per-service wash counts. Read-only for the customer — no extension.
 */
export interface CustomPlanPassView {
  id: string;
  status?: string | null;
  remaining: number;
  total: number;
  remaining_by_service: Record<string, number>;
  start_date?: string | null;
  end_date?: string | null;
  extension_days?: number | null;
  extended_until?: string | null;
  /** YYYY-MM-DD — the last day a wash can be booked on. */
  last_bookable_day?: string | null;
  /** "18 Oct 2026" — shown as "Last Booking Day: 18 Oct 2026". */
  last_booking_day_label?: string | null;
  in_extension?: boolean;
  /** A renewal lined up after this pass. */
  renewed_by_subscription_id?: string | null;
}

/** A car's refund (custom-plan car refunded to the wallet). */
export interface CustomPlanRefund {
  amount?: number | null;
  at?: string | null;
  reason?: string | null;
  washes?: number | null;
}

export interface CustomPlanItem {
  service_id: string;
  service_name?: string | null;
  count: number;
  remaining: number;
  unit_price?: number;
  line_total?: number;
}

export interface CustomPlanCar {
  vehicle_id?: string | null;
  registration_number?: string | null;
  vehicle_type?: string | null;
  vehicle_type_name?: string | null;
  items: CustomPlanItem[];
  washes?: number | null;
  amount?: number | null;
  /** pending | active | skipped | refunded … */
  status?: string | null;
  refund?: CustomPlanRefund | null;
  /** YYYY-MM-DD — when this car's 30 days start (a renewal: projected if paid now). */
  starts_on?: string | null;
  starts_on_label?: string | null;
  /** A renewal car: the old pass it renews. */
  renews_subscription_id?: string | null;
  subscription_id?: string | null;
  /** status "scheduled" = a paid renewal waiting for its start. */
  subscription?: CustomPlanPassView | null;
}

export type CustomPlanStatus = "draft" | "awaiting_payment" | "activating" | "active" | "needs_review" | "refunded" | "cancelled";

export interface CustomPlanCart {
  id: string;
  /** The center that sold it — its slots are the ones offered. */
  service_center_id?: string | null;
  plan_name?: string | null;
  status: CustomPlanStatus | string;
  revision: number;
  cars: CustomPlanCar[];
  car_count: number;
  washes: number;
  subtotal?: number | null;
  discount_amount?: number | null;
  total_amount?: number | null;
  period_days?: number | null;
  period_start?: string | null;
  period_end?: string | null;
  /** The open Razorpay link for the current revision (unpaid carts only). */
  payment_link?: { short_url?: string | null; amount?: number | null; order_id?: string | null } | null;
  /** Renewal: this cart renews `renewal_of`; an old cart names its renewal in `renewal_cart_id`. */
  renewal_of?: string | null;
  renewal_cart_id?: string | null;
  /** YYYY-MM-DD / "18 Oct 2026" — the renewal's first day. */
  renewal_starts_on?: string | null;
  renewal_starts_on_label?: string | null;
  /** The unpaid cart's button: "Pay ₹1499 To Renew" / "Pay ₹1499 To Activate". */
  pay_label?: string | null;
  refunded_amount?: number | null;
  created_at?: string | null;
  activated_at?: string | null;
}

export const customPlansMeApi = {
  my: () => apiClient.get<ApiSuccess<CustomPlanCart[]>>("/subscriptions/custom-plans/my").then((r) => r.data.data),
};

export const MY_CUSTOM_PLANS_QUERY_KEY = ["my-custom-plans"] as const;
