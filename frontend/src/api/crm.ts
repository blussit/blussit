import { apiClient, type ApiSuccess } from "../lib/api-client";
import type { Address, Complaint, User, UserSubscription, Vehicle } from "../types";

export interface Customer360 {
  profile: User;
  bookings: unknown[];
  subscriptions: UserSubscription[];
  complaints: Complaint[];
  vehicles: Vehicle[];
  addresses: Address[];
  lifetime_spend: number;
  last_service_date: string | null;
  preferred_service_center_id: string | null;
  total_bookings: number;
}

export const crmApi = {
  searchCustomerByPhone: (phone: string) => apiClient.get<ApiSuccess<User | null>>("/crm/customers/search", { params: { phone } }).then((r) => r.data.data),
  customer360: (id: string) => apiClient.get<ApiSuccess<Customer360>>(`/crm/customers/${id}`).then((r) => r.data.data),
};
