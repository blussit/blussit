import { apiClient, type ApiSuccess } from "../lib/api-client";
import type { Address, User, Vehicle } from "../types";

export const vehicleApi = {
  list: () => apiClient.get<ApiSuccess<Vehicle[]>>("/vehicles").then((r) => r.data.data),
  create: (payload: Partial<Vehicle>) => apiClient.post<ApiSuccess<Vehicle>>("/vehicles", payload).then((r) => r.data.data),
  update: (id: string, payload: Partial<Vehicle>) => apiClient.put<ApiSuccess<Vehicle>>(`/vehicles/${id}`, payload).then((r) => r.data.data),
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
