import { apiClient, type ApiSuccess } from "../lib/api-client";
import type { Address, User, Vehicle } from "../types";

/**
 * One car in My Garage (GET /vehicles/garage): a saved vehicle and/or a car
 * from the customer's booking history, merged server-side — one row per
 * vehicle record, else per plate, else per vehicle type (plateless).
 */
export interface GarageCar {
  /** Stable row key: "v:<vehicleId>" | "p:<plate>" | "t:<vehicleTypeId>". */
  id: string;
  /** Set only for a saved vehicle (edit/remove apply). */
  vehicle_id: string | null;
  saved: boolean;
  is_default: boolean;
  vehicle_type: string | null;
  vehicle_type_name: string;
  brand: string | null;
  model: string | null;
  registration_number: string | null;
  /** YYYY-MM-DD (IST) of the newest completed wash. */
  last_washed_on: string | null;
  wash_count: number;
  /** The soonest open booking from today on. */
  next_wash_on: string | null;
  next_wash_slot: string | null;
  next_booking_id: string | null;
  /** Newest finished single-car booking — what "Clean again" replays. */
  repeat_booking_id: string | null;
  last_booking_id: string | null;
  /** Service(s) on the next visit / the last finished wash, e.g. "Star Wash". */
  next_service_name?: string | null;
  last_wash_booking_id?: string | null;
  last_wash_service_name?: string | null;
}

export const vehicleApi = {
  list: () => apiClient.get<ApiSuccess<Vehicle[]>>("/vehicles").then((r) => r.data.data),
  garage: () => apiClient.get<ApiSuccess<GarageCar[]>>("/vehicles/garage").then((r) => r.data.data),
  // Never trust this alone as the real guard — the backend re-checks at
  // actual create/update time regardless — it's purely so the frontend can
  // show the "already registered elsewhere — Continue/Cancel" dialog
  // before submitting.
  checkRegistration: (registration_number: string) =>
    apiClient.post<ApiSuccess<{ already_registered: boolean; other_account_count: number }>>("/vehicles/check-registration", { registration_number }).then((r) => r.data.data),
  create: (payload: Partial<Vehicle> & { acknowledge_shared_registration?: boolean }) =>
    apiClient.post<ApiSuccess<Vehicle>>("/vehicles", payload).then((r) => r.data.data),
  update: (id: string, payload: Partial<Vehicle> & { acknowledge_shared_registration?: boolean }) =>
    apiClient.put<ApiSuccess<Vehicle>>(`/vehicles/${id}`, payload).then((r) => r.data.data),
  remove: (id: string) => apiClient.delete(`/vehicles/${id}`).then((r) => r.data),
};

export const addressApi = {
  list: () => apiClient.get<ApiSuccess<Address[]>>("/addresses").then((r) => r.data.data),
  create: (payload: Partial<Address>) => apiClient.post<ApiSuccess<Address>>("/addresses", payload).then((r) => r.data.data),
  update: (id: string, payload: Partial<Address>) => apiClient.put<ApiSuccess<Address>>(`/addresses/${id}`, payload).then((r) => r.data.data),
  remove: (id: string) => apiClient.delete(`/addresses/${id}`).then((r) => r.data),
  /** Staff only (manager/admin): an existing customer's saved addresses. */
  forCustomer: (customerId: string) => apiClient.get<ApiSuccess<Address[]>>(`/addresses/customer/${customerId}`).then((r) => r.data.data),
};

/** What PUT /users/me accepts from the signed-in user. */
export type ProfileUpdatePayload = Partial<Pick<User, "full_name" | "profile_image" | "marketing_opt_out">>;

export const userApi = {
  updateProfile: (payload: ProfileUpdatePayload) => apiClient.put<ApiSuccess<User>>("/users/me", payload).then((r) => r.data.data),
};
