import { apiClient, type ApiPaginated, type ApiSuccess } from "../lib/api-client";
import type { Complaint, Coupon, Notification, Review, SubscriptionPlan, UserSubscription } from "../types";

/** One subscription row on the manager's center overview. */
export interface CenterSubscriptionRow {
  subscription_id: string;
  customer_id: string;
  customer_name: string;
  customer_phone?: string | null;
  plan_id?: string | null;
  plan_name: string;
  status?: string | null;
  vehicle_type?: string | null;
  vehicle_type_name?: string | null;
  purchased_price?: number | null;
  remaining_service_count?: number | null;
  total_service_count?: number | null;
  start_date?: string | null;
  end_date?: string | null;
  days_left?: number | null;
}

export interface CenterSubscriptionOverview {
  kpis: { total: number; active: number; expiring_soon: number; expired: number };
  plan_breakdown: { plan_name: string; active_count: number }[];
  rows: CenterSubscriptionRow[];
}

/** One row on the admin's "all purchased plans" view — every subscription
 *  platform-wide, who paid what and which center (if any) sold it. */
export interface AdminSubscriptionRow {
  subscription_id: string;
  customer_id: string;
  customer_name: string;
  customer_phone?: string | null;
  plan_id?: string | null;
  plan_name: string;
  status?: string | null;
  amount_paid?: number | null;
  discount_amount?: number | null;
  coupon_code?: string | null;
  payment_method?: "online" | "cash" | null;
  auto_renew: boolean;
  service_center_id?: string | null;
  service_center_name?: string | null;
  start_date?: string | null;
  end_date?: string | null;
}

export interface AdminSubscriptionOverview {
  kpis: { total: number; active: number; expired: number; total_revenue: number };
  plan_breakdown: { plan_name: string; count: number }[];
  rows: AdminSubscriptionRow[];
}

/** What a monthly pass would cost for one car + one service. Priced by the
 *  same backend code that charges for it. */
export interface PassQuote {
  plan_id: string;
  plan_name: string;
  vehicle_id: string;
  vehicle_type: string;
  service_id: string;
  service_name: string;
  visits: number;
  price_per_wash: number;
  price: number;
  discount_percent: number;
  /** This car already has a live pass — one car carries one pass. */
  vehicle_has_pass: boolean;
}

/** Live price for the manager's "sell a plan" form — see
 *  PaymentService.manager_subscription_preview. No side effects. */
export interface ManagerOfferPreview {
  plan_name: string;
  service_name: string;
  visits: number;
  price_per_wash: number;
  base_price: number;
  discount: number;
  final_price: number;
  /** null = no coupon code typed yet. */
  coupon_valid: boolean | null;
  coupon_error?: string | null;
  customer_exists: boolean;
  /** This phone already holds a live pass for this vehicle type + service. */
  already_has_pass: boolean;
}

export interface ManagerOfferPayload {
  customer_name: string;
  customer_phone: string;
  plan_id: string;
  vehicle_type: string;
  service_id: string;
  recurring: boolean;
  payment_method: "link" | "cash";
  discount_amount?: number;
  coupon_code?: string;
  send_whatsapp: boolean;
}

export interface ManagerOfferResult {
  kind: "link" | "autopay" | "cash";
  recurring: boolean;
  amount: number;
  short_url?: string;
  order_id?: string;
  subscription?: UserSubscription;
}

export interface PlanEnquiryPayload {
  name: string;
  phone: string;
  vehicle_count: number;
  services_wanted: string;
  washes_per_month?: number;
  preferred_time?: string;
  notes?: string;
}

export const subscriptionApi = {
  quotePass: (payload: { plan_id: string; vehicle_id?: string; vehicle_type?: string; service_id: string }) =>
    apiClient.post<ApiSuccess<PassQuote>>("/subscriptions/quote", payload).then((r) => r.data.data),
  submitEnquiry: (payload: PlanEnquiryPayload) =>
    apiClient.post<ApiSuccess<null>>("/subscriptions/enquiries", payload).then((r) => r.data),
  centerOverview: (centerId: string) =>
    apiClient.get<ApiSuccess<CenterSubscriptionOverview>>(`/subscriptions/center/${centerId}/overview`).then((r) => r.data.data),
  /** Admin-only: every plan ever purchased or granted, platform-wide. */
  adminOverview: () => apiClient.get<ApiSuccess<AdminSubscriptionOverview>>("/subscriptions/admin/overview").then((r) => r.data.data),
  plans: (activeOnly = true) =>
    apiClient.get<ApiSuccess<SubscriptionPlan[]>>("/subscription-plans", { params: { active_only: activeOnly } }).then((r) => r.data.data),
  mySubscriptions: () => apiClient.get<ApiSuccess<UserSubscription[]>>("/subscriptions/my").then((r) => r.data.data),
  forCustomer: (customerId: string) =>
    apiClient.get<ApiSuccess<UserSubscription[]>>(`/subscriptions/customer/${customerId}`).then((r) => r.data.data),
  assign: (payload: { customer_id: string; plan_id: string; vehicle_type?: string; auto_renew?: boolean }) =>
    apiClient.post<ApiSuccess<UserSubscription>>("/subscriptions/assign", payload).then((r) => r.data.data),
  subscribe: (payload: { plan_id: string; vehicle_type?: string; auto_renew?: boolean }) =>
    apiClient.post<ApiSuccess<UserSubscription>>("/subscriptions", payload).then((r) => r.data.data),
  cancel: (id: string) => apiClient.post<ApiSuccess<UserSubscription>>(`/subscriptions/${id}/cancel`).then((r) => r.data.data),
  upgrade: (id: string, newPlanId: string) =>
    apiClient.post<ApiSuccess<UserSubscription>>(`/subscriptions/${id}/upgrade`, { new_plan_id: newPlanId }).then((r) => r.data.data),
  /** Turn auto-pay OFF (the mandate is cancelled at the end of the cycle
   *  already paid for). Turning it back ON needs a fresh authorisation and
   *  is refused by the API — buy the plan again to restart it. */
  setAutoPay: (id: string, enabled: boolean) =>
    apiClient.post<ApiSuccess<UserSubscription>>(`/subscriptions/${id}/auto-pay`, { enabled }).then((r) => r.data.data),
  adminAll: (params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<UserSubscription>>("/subscriptions/admin/all", { params }).then((r) => r.data),
  /** Manager/admin sells a plan: a WhatsApp payment link (one-time,
   *  optionally discounted/coupon'd), a full-rate auto-pay link, or cash
   *  collected on the spot. See PaymentService.manager_subscription_offer. */
  managerOfferPreview: (payload: Partial<ManagerOfferPayload>) =>
    apiClient.post<ApiSuccess<ManagerOfferPreview>>("/subscriptions/manager-offers/preview", payload).then((r) => r.data.data),
  managerOfferCreate: (payload: ManagerOfferPayload) =>
    apiClient.post<ApiSuccess<ManagerOfferResult>>("/subscriptions/manager-offers", payload).then((r) => r.data.data),
  managerOfferVoid: (orderId: string) =>
    apiClient.post<ApiSuccess<{ voided: boolean }>>(`/subscriptions/manager-offers/${orderId}/void`).then((r) => r.data.data),
  /** Stop SELLING a plan — is_active only, price/contents untouched.
   *  Manager-safe (unlike the full PUT edit, which stays admin-only). */
  discontinuePlan: (planId: string) =>
    apiClient.post<ApiSuccess<SubscriptionPlan>>(`/subscription-plans/${planId}/discontinue`).then((r) => r.data.data),
};

export const couponApi = {
  publicOffer: (code: string) => apiClient.get<ApiSuccess<Coupon>>(`/coupons/public/${code}`).then((r) => r.data.data),
  validate: (code: string, orderValue: number) =>
    apiClient
      .post<ApiSuccess<{ valid: boolean; discount_amount: number; coupon_code: string }>>("/coupons/validate", {
        code,
        order_value: orderValue,
      })
      .then((r) => r.data.data),
};

export const reviewApi = {
  mine: () => apiClient.get<ApiSuccess<Review[]>>("/reviews/my").then((r) => r.data.data),
  create: (payload: { booking_id: string; captain_rating?: number; captain_comment?: string; service_rating: number; service_comment?: string }) =>
    apiClient.post<ApiSuccess<Review>>("/reviews", payload).then((r) => r.data.data),
  update: (id: string, payload: { captain_rating?: number; captain_comment?: string; service_rating?: number; service_comment?: string }) =>
    apiClient.put<ApiSuccess<Review>>(`/reviews/${id}`, payload).then((r) => r.data.data),
  remove: (id: string) => apiClient.delete(`/reviews/${id}`).then((r) => r.data),
  captainSummary: (captainId: string) =>
    apiClient.get<ApiSuccess<{ avg_rating: number; count: number }>>(`/reviews/captain/${captainId}/summary`).then((r) => r.data.data),
  captainReviews: (captainId: string) =>
    apiClient.get<ApiSuccess<Review[]>>(`/reviews/captain/${captainId}`).then((r) => r.data.data),
  forBooking: (bookingId: string) => apiClient.get<ApiSuccess<Review | null>>(`/reviews/booking/${bookingId}`).then((r) => r.data.data),
  // Denormalized (customer/captain/service-center names resolved) —
  // Section 12's Reviews page for admin (all centers) / manager (own
  // center only, backend-enforced).
  forCenter: (serviceCenterId: string, params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<Review>>(`/reviews/center/${serviceCenterId}`, { params }).then((r) => r.data),
  forAdmin: (params?: { page?: number; page_size?: number; include_deleted?: boolean }) =>
    apiClient.get<ApiPaginated<Review>>("/reviews/admin/all", { params }).then((r) => r.data),
};

export const notificationApi = {
  list: (params?: { unread_only?: boolean; page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<Notification> & { unread_count: number }>("/notifications", { params }).then((r) => r.data),
  markRead: (id: string) => apiClient.post<ApiSuccess<Notification>>(`/notifications/${id}/read`).then((r) => r.data.data),
  markAllRead: () => apiClient.post("/notifications/read-all").then((r) => r.data),
};

export const complaintApi = {
  create: (payload: { booking_id: string; subject: string; description: string; priority?: string }) =>
    apiClient.post<ApiSuccess<Complaint>>("/complaints", payload).then((r) => r.data.data),
  mine: (params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<Complaint>>("/complaints/my", { params }).then((r) => r.data),
  forCenter: (serviceCenterId: string, params?: { status?: string; page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<Complaint>>(`/complaints/center/${serviceCenterId}`, { params }).then((r) => r.data),
  all: (params?: { status?: string; page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<Complaint>>("/complaints", { params }).then((r) => r.data),
  update: (id: string, payload: { status?: string; priority?: string; resolution_note?: string }) =>
    apiClient.put<ApiSuccess<Complaint>>(`/complaints/${id}`, payload).then((r) => r.data.data),
  reply: (id: string, payload: { message: string; status?: string }) =>
    apiClient.post<ApiSuccess<Complaint>>(`/complaints/${id}/reply`, payload).then((r) => r.data.data),
};
