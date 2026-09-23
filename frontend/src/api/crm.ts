import { apiClient, type ApiSuccess } from "../lib/api-client";
import type { Address, Booking, Complaint, User, UserSubscription, Vehicle } from "../types";

/** A customer-360 booking row — the same Booking shape, plus the display
 *  names CRMService.get_customer_360 resolves server-side. */
export type Customer360Booking = Booking & {
  vehicle_label?: string | null;
  service_names?: string[];
  address_text?: string | null;
  service_center_name?: string | null;
};

export type Customer360Subscription = UserSubscription & { plan_name?: string };
export type Customer360Vehicle = Vehicle & { vehicle_type_name?: string | null };

export interface Customer360 {
  profile: User;
  bookings: Customer360Booking[];
  subscriptions: Customer360Subscription[];
  complaints: Complaint[];
  vehicles: Customer360Vehicle[];
  addresses: Address[];
  lifetime_spend: number;
  /** Total ever paid across all subscriptions/plans — separate from booking spend. */
  lifetime_plan_spend: number;
  /** lifetime_spend + lifetime_plan_spend — "how much has this customer actually brought in". */
  lifetime_total_spend: number;
  last_service_date: string | null;
  preferred_service_center_id: string | null;
  preferred_service_center_name?: string | null;
  total_bookings: number;
  /** YYYY-MM-DD (IST) dates this customer created 2+ bookings on. */
  same_day_repeat_dates: string[];
}

export const crmApi = {
  searchCustomerByPhone: (phone: string) => apiClient.get<ApiSuccess<User | null>>("/crm/customers/search", { params: { phone } }).then((r) => r.data.data),
  customerTypeahead: (q: string) => apiClient.get<ApiSuccess<User[]>>("/crm/customers/typeahead", { params: { q } }).then((r) => r.data.data),
  customer360: (id: string) => apiClient.get<ApiSuccess<Customer360>>(`/crm/customers/${id}`).then((r) => r.data.data),
};
