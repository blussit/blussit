import { apiClient, type ApiPaginated, type ApiSuccess } from "../lib/api-client";
import type { Complaint, Notification, Review, SubscriptionPlan, UserSubscription } from "../types";

export const subscriptionApi = {
  plans: (activeOnly = true) =>
    apiClient.get<ApiSuccess<SubscriptionPlan[]>>("/subscription-plans", { params: { active_only: activeOnly } }).then((r) => r.data.data),
  mySubscriptions: () => apiClient.get<ApiSuccess<UserSubscription[]>>("/subscriptions/my").then((r) => r.data.data),
  forCustomer: (customerId: string) =>
    apiClient.get<ApiSuccess<UserSubscription[]>>(`/subscriptions/customer/${customerId}`).then((r) => r.data.data),
  assign: (payload: { customer_id: string; plan_id: string; vehicle_id: string; auto_renew?: boolean }) =>
    apiClient.post<ApiSuccess<UserSubscription>>("/subscriptions/assign", payload).then((r) => r.data.data),
  subscribe: (payload: { plan_id: string; vehicle_id?: string; auto_renew?: boolean }) =>
    apiClient.post<ApiSuccess<UserSubscription>>("/subscriptions", payload).then((r) => r.data.data),
  cancel: (id: string) => apiClient.post<ApiSuccess<UserSubscription>>(`/subscriptions/${id}/cancel`).then((r) => r.data.data),
  upgrade: (id: string, newPlanId: string) =>
    apiClient.post<ApiSuccess<UserSubscription>>(`/subscriptions/${id}/upgrade`, { new_plan_id: newPlanId }).then((r) => r.data.data),
  adminAll: (params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<UserSubscription>>("/subscriptions/admin/all", { params }).then((r) => r.data),
};

export const couponApi = {
  validate: (code: string, orderValue: number) =>
    apiClient
      .post<ApiSuccess<{ valid: boolean; discount_amount: number; coupon_code: string }>>("/coupons/validate", {
        code,
        order_value: orderValue,
      })
      .then((r) => r.data.data),
};

export const reviewApi = {
  listPublic: (params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<Review>>("/reviews", { params }).then((r) => r.data),
  create: (payload: { booking_id: string; rating: number; comment?: string }) =>
    apiClient.post<ApiSuccess<Review>>("/reviews", payload).then((r) => r.data.data),
  captainSummary: (captainId: string) =>
    apiClient.get<ApiSuccess<{ avg_rating: number; count: number }>>(`/reviews/captain/${captainId}/summary`).then((r) => r.data.data),
  captainReviews: (captainId: string) =>
    apiClient.get<ApiSuccess<Review[]>>(`/reviews/captain/${captainId}`).then((r) => r.data.data),
};

export const notificationApi = {
  list: (params?: { unread_only?: boolean; page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<Notification> & { unread_count: number }>("/notifications", { params }).then((r) => r.data),
  markRead: (id: string) => apiClient.post<ApiSuccess<Notification>>(`/notifications/${id}/read`).then((r) => r.data.data),
  markAllRead: () => apiClient.post("/notifications/read-all").then((r) => r.data),
};

export const complaintApi = {
  create: (payload: { booking_id?: string; subject: string; description: string; priority?: string }) =>
    apiClient.post<ApiSuccess<Complaint>>("/complaints", payload).then((r) => r.data.data),
  mine: (params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<Complaint>>("/complaints/my", { params }).then((r) => r.data),
  forCenter: (serviceCenterId: string, params?: { status?: string; page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<Complaint>>(`/complaints/center/${serviceCenterId}`, { params }).then((r) => r.data),
  all: (params?: { status?: string; page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<Complaint>>("/complaints", { params }).then((r) => r.data),
  update: (id: string, payload: { status?: string; priority?: string; resolution_note?: string }) =>
    apiClient.put<ApiSuccess<Complaint>>(`/complaints/${id}`, payload).then((r) => r.data.data),
};
