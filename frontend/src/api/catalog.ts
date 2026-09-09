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

export const coverageLeadPublicApi = {
  // Public by design — fires exactly when a visitor's area fails the
  // coverage check, before any account exists (see backend route docstring).
  capture: (payload: { name: string; phone: string; pincode: string; city_area?: string; service_interest?: string }) =>
    apiClient.post<ApiSuccess<null>>("/coverage-leads", payload).then((r) => r.data),
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

// ---- Slot holds (theater-seat model) --------------------------------------
/** Stable per-browser holder key; the backend converts the hold into the
 * booking when the same key is sent as hold_key at create time. */
export function getSlotHolderKey(): string {
  const KEY = "dvc_slot_holder";
  try {
    let k = sessionStorage.getItem(KEY);
    if (!k) {
      k = Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
      sessionStorage.setItem(KEY, k);
    }
    return k;
  } catch {
    return "anon-" + Math.random().toString(36).slice(2, 14);
  }
}

export const slotHoldApi = {
  hold: (service_center_id: string, date: string, slot_key: string) =>
    apiClient
      .post<ApiSuccess<{ held: boolean; renewed: boolean; expires_at: string; hold_seconds: number }>>("/bookings/hold", {
        service_center_id, date, slot_key, holder_key: getSlotHolderKey(),
      })
      .then((r) => r.data.data),
  release: (service_center_id: string, date: string, slot_key: string) =>
    apiClient.post("/bookings/hold/release", { service_center_id, date, slot_key, holder_key: getSlotHolderKey() }).catch(() => undefined),
};

export const coverageApi = {
  check: (payload: { latitude?: number; longitude?: number; pincode?: string }) =>
    apiClient
      .post<ApiSuccess<{ covered: boolean; center: { id: string; name: string; city: string | null; state: string | null } | null; distance_km?: number }>>(
        "/service-zones/coverage-check",
        payload,
      )
      .then((r) => r.data.data),
};
