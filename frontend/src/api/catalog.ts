import { apiClient, type ApiPaginated, type ApiSuccess } from "../lib/api-client";
import type { BookingPolicy, Category, ComboOffer, HomepageConfig, Service, ServiceCenter, SlotAvailability, VehicleTypeOption } from "../types";

export const catalogApi = {
  categories: (activeOnly = true) =>
    apiClient.get<ApiSuccess<Category[]>>("/categories", { params: { active_only: activeOnly } }).then((r) => r.data.data),

  services: (params?: { category_id?: string; vehicle_type?: string; search?: string; active_only?: boolean; page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<Service>>("/services", { params }).then((r) => r.data),

  service: (id: string) => apiClient.get<ApiSuccess<Service>>(`/services/${id}`).then((r) => r.data.data),
};

export const vehicleTypeApi = {
  list: (activeOnly = true) =>
    apiClient.get<ApiSuccess<VehicleTypeOption[]>>("/vehicle-types", { params: { active_only: activeOnly } }).then((r) => r.data.data),
};

export const comboOfferApi = {
  list: (activeOnly = false) =>
    apiClient.get<ApiSuccess<ComboOffer[]>>("/combo-offers", { params: { active_only: activeOnly } }).then((r) => r.data.data),
  get: (id: string) => apiClient.get<ApiSuccess<ComboOffer>>(`/combo-offers/${id}`).then((r) => r.data.data),
};

export const bookingPolicyApi = {
  get: () => apiClient.get<ApiSuccess<BookingPolicy>>("/booking-policy").then((r) => r.data.data),
};

export const homepageConfigApi = {
  get: () => apiClient.get<ApiSuccess<HomepageConfig>>("/homepage-config").then((r) => r.data.data),
};

export const serviceCenterApi = {
  lookupByPincode: (pincode: string) =>
    apiClient.get<ApiSuccess<ServiceCenter[]>>("/service-centers/lookup", { params: { pincode } }).then((r) => r.data.data),
  // Never exposes raw capacity — see SlotAvailability. Short refetchInterval
  // recommended at call sites since capacity can change while the picker
  // is open (real-time availability = polling + an authoritative re-check
  // at submit time, not a websocket push).
  availableSlots: (centerId: string, date: string) =>
    apiClient.get<ApiSuccess<SlotAvailability[]>>(`/service-centers/${centerId}/slots`, { params: { date } }).then((r) => r.data.data),
};

export const contentApi = {
  faqs: () => apiClient.get<ApiSuccess<{ id: string; question: string; answer: string }[]>>("/faqs").then((r) => r.data.data),
  testimonials: () =>
    apiClient
      .get<ApiSuccess<{ id: string; customer_name: string; rating: number; comment: string }[]>>("/testimonials")
      .then((r) => r.data.data),
  publicStats: () =>
    apiClient
      .get<ApiSuccess<{ vehicles_serviced: number; happy_customers: number; service_centers: number; average_rating: number }>>(
        "/public/stats"
      )
      .then((r) => r.data.data),
  submitContactMessage: (payload: { name: string; phone: string; email: string; message: string }) =>
    apiClient.post<ApiSuccess<Record<string, unknown>>>("/contact", payload).then((r) => r.data),
};
