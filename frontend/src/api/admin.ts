import { apiClient, type ApiPaginated, type ApiSuccess } from "../lib/api-client";
import type { BookingPolicy, Category, ComboOffer, ContactMessage, Coupon, HomepageConfig, InventoryItem, PricingConfig, Service, ServiceCenter, SubscriptionPlan, User, VehicleTypeOption } from "../types";

export const adminPricingApi = {
  get: () => apiClient.get<ApiSuccess<PricingConfig>>("/pricing-config").then((r) => r.data.data),
  set: (per_km_rate: number, default_captain_service_fee: number) =>
    apiClient.put<ApiSuccess<PricingConfig>>("/pricing-config", { per_km_rate, default_captain_service_fee }).then((r) => r.data.data),
};

export const adminBookingPolicyApi = {
  get: () => apiClient.get<ApiSuccess<BookingPolicy>>("/booking-policy").then((r) => r.data.data),
  set: (payload: Partial<BookingPolicy>) => apiClient.put<ApiSuccess<BookingPolicy>>("/booking-policy", payload).then((r) => r.data.data),
};

export const adminHomepageConfigApi = {
  get: () => apiClient.get<ApiSuccess<HomepageConfig>>("/homepage-config").then((r) => r.data.data),
  set: (payload: Partial<HomepageConfig>) => apiClient.put<ApiSuccess<HomepageConfig>>("/homepage-config", payload).then((r) => r.data.data),
};

export const adminContactMessageApi = {
  list: (params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<ContactMessage>>("/contact", { params }).then((r) => r.data),
};

export const adminComboOfferApi = {
  list: () => apiClient.get<ApiSuccess<ComboOffer[]>>("/combo-offers").then((r) => r.data.data),
  create: (payload: Partial<ComboOffer>) => apiClient.post<ApiSuccess<ComboOffer>>("/combo-offers", payload).then((r) => r.data.data),
  update: (id: string, payload: Partial<ComboOffer>) => apiClient.put<ApiSuccess<ComboOffer>>(`/combo-offers/${id}`, payload).then((r) => r.data.data),
  remove: (id: string) => apiClient.delete(`/combo-offers/${id}`).then((r) => r.data),
};

export const adminUserApi = {
  list: (params?: { role?: string; page?: number; page_size?: number; search?: string }) =>
    apiClient.get<ApiPaginated<User>>("/users", { params }).then((r) => r.data),
  get: (id: string) => apiClient.get<ApiSuccess<User>>(`/users/${id}`).then((r) => r.data.data),
  update: (id: string, payload: Partial<User>) => apiClient.put<ApiSuccess<User>>(`/users/${id}`, payload).then((r) => r.data.data),
  suspend: (id: string) => apiClient.post<ApiSuccess<User>>(`/users/${id}/suspend`).then((r) => r.data.data),
  remove: (id: string) => apiClient.delete(`/users/${id}`).then((r) => r.data),
  createStaff: (payload: { full_name: string; email?: string; phone?: string; password: string; role: string; service_center_id?: string }) =>
    apiClient.post<ApiSuccess<User>>("/auth/staff", payload).then((r) => r.data.data),
  // Manager/admin creating a customer on their behalf (e.g. a phone/walk-in
  // booking) — the account gets a temp password the customer must change on
  // first login, see User.must_change_password.
  createCustomer: (payload: { full_name: string; phone: string; email?: string; temp_password: string }) =>
    apiClient.post<ApiSuccess<User>>("/auth/customers", payload).then((r) => r.data.data),
};

export const adminServiceCenterApi = {
  list: (params?: { page?: number; page_size?: number; search?: string; active_only?: boolean }) =>
    apiClient.get<ApiPaginated<ServiceCenter>>("/service-centers", { params }).then((r) => r.data),
  get: (id: string) => apiClient.get<ApiSuccess<ServiceCenter>>(`/service-centers/${id}`).then((r) => r.data.data),
  create: (payload: Partial<ServiceCenter>) => apiClient.post<ApiSuccess<ServiceCenter>>("/service-centers", payload).then((r) => r.data.data),
  update: (id: string, payload: Partial<ServiceCenter>) =>
    apiClient.put<ApiSuccess<ServiceCenter>>(`/service-centers/${id}`, payload).then((r) => r.data.data),
  remove: (id: string) => apiClient.delete(`/service-centers/${id}`).then((r) => r.data),
};

export const adminVehicleTypeApi = {
  create: (payload: { name: string; display_order?: number; is_active?: boolean }) =>
    apiClient.post<ApiSuccess<VehicleTypeOption>>("/vehicle-types", payload).then((r) => r.data.data),
  update: (id: string, payload: Partial<{ name: string; display_order: number; is_active: boolean }>) =>
    apiClient.put<ApiSuccess<VehicleTypeOption>>(`/vehicle-types/${id}`, payload).then((r) => r.data.data),
  remove: (id: string) => apiClient.delete(`/vehicle-types/${id}`).then((r) => r.data),
};

export const adminCatalogApi = {
  createCategory: (payload: Partial<Category>) => apiClient.post<ApiSuccess<Category>>("/categories", payload).then((r) => r.data.data),
  updateCategory: (id: string, payload: Partial<Category>) =>
    apiClient.put<ApiSuccess<Category>>(`/categories/${id}`, payload).then((r) => r.data.data),
  deleteCategory: (id: string) => apiClient.delete(`/categories/${id}`).then((r) => r.data),

  createService: (payload: Partial<Service>) => apiClient.post<ApiSuccess<Service>>("/services", payload).then((r) => r.data.data),
  updateService: (id: string, payload: Partial<Service>) =>
    apiClient.put<ApiSuccess<Service>>(`/services/${id}`, payload).then((r) => r.data.data),
  deleteService: (id: string) => apiClient.delete(`/services/${id}`).then((r) => r.data),
};

export const adminSubscriptionPlanApi = {
  create: (payload: Partial<SubscriptionPlan>) => apiClient.post<ApiSuccess<SubscriptionPlan>>("/subscription-plans", payload).then((r) => r.data.data),
  update: (id: string, payload: Partial<SubscriptionPlan>) =>
    apiClient.put<ApiSuccess<SubscriptionPlan>>(`/subscription-plans/${id}`, payload).then((r) => r.data.data),
  remove: (id: string) => apiClient.delete(`/subscription-plans/${id}`).then((r) => r.data),
};

export const adminCouponApi = {
  list: (params?: { page?: number; page_size?: number; active_only?: boolean }) =>
    apiClient.get<ApiPaginated<Coupon>>("/coupons", { params }).then((r) => r.data),
  create: (payload: Partial<Coupon>) => apiClient.post<ApiSuccess<Coupon>>("/coupons", payload).then((r) => r.data.data),
  update: (id: string, payload: Partial<Coupon>) => apiClient.put<ApiSuccess<Coupon>>(`/coupons/${id}`, payload).then((r) => r.data.data),
  remove: (id: string) => apiClient.delete(`/coupons/${id}`).then((r) => r.data),
};

export const inventoryApi = {
  forCenter: (serviceCenterId: string, params?: { page?: number; page_size?: number; low_stock_only?: boolean }) =>
    apiClient.get<ApiPaginated<InventoryItem>>(`/inventory/center/${serviceCenterId}`, { params }).then((r) => r.data),
  create: (payload: Partial<InventoryItem>) => apiClient.post<ApiSuccess<InventoryItem>>("/inventory", payload).then((r) => r.data.data),
  update: (id: string, payload: Partial<InventoryItem>) =>
    apiClient.put<ApiSuccess<InventoryItem>>(`/inventory/${id}`, payload).then((r) => r.data.data),
  adjust: (id: string, delta: number, reason?: string) =>
    apiClient.post<ApiSuccess<InventoryItem>>(`/inventory/${id}/adjust`, { delta, reason }).then((r) => r.data.data),
  remove: (id: string) => apiClient.delete(`/inventory/${id}`).then((r) => r.data),
};

export interface CaptainPerformance {
  booking_status_counts: Record<string, number>;
  average_rating: number;
  total_reviews: number;
  total_jobs_completed: number;
  total_earnings: number;
  avg_heading_punctuality_minutes: number | null;
}

export const staffDirectoryApi = {
  captainsForCenter: (serviceCenterId: string, params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<User>>(`/staff/captains/center/${serviceCenterId}`, { params }).then((r) => r.data),
  captainPerformance: (captainId: string) =>
    apiClient.get<ApiSuccess<CaptainPerformance>>(`/staff/captains/${captainId}/performance`).then((r) => r.data.data),
  myPerformance: () => apiClient.get<ApiSuccess<CaptainPerformance>>("/staff/my-performance").then((r) => r.data.data),
};

export const analyticsApi = {
  dashboard: () => apiClient.get<ApiSuccess<Record<string, unknown>>>("/analytics/dashboard").then((r) => r.data.data),
  bookingTrends: (days = 30) =>
    apiClient
      .get<ApiSuccess<{ date: string; bookings: number; revenue: number }[]>>("/analytics/booking-trends", { params: { days } })
      .then((r) => r.data.data),
};

export const crmApi = {
  customer360: (customerId: string) => apiClient.get<ApiSuccess<Record<string, unknown>>>(`/crm/customers/${customerId}`).then((r) => r.data.data),
};

export const auditLogApi = {
  list: (params?: { module?: string; actor_id?: string; page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<Record<string, unknown>>>("/audit-logs", { params }).then((r) => r.data),
};
