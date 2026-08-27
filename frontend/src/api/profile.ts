import { apiClient, type ApiSuccess } from "../lib/api-client";
import type { Address, User, Vehicle } from "../types";

export const vehicleApi = {
  list: () => apiClient.get<ApiSuccess<Vehicle[]>>("/vehicles").then((r) => r.data.data),
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
};

export const userApi = {
  updateProfile: (payload: Partial<User>) => apiClient.put<ApiSuccess<User>>("/users/me", payload).then((r) => r.data.data),
};
