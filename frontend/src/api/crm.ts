import { apiClient, type ApiSuccess } from "../lib/api-client";
import type { Address, Booking, Complaint, User, UserSubscription, Vehicle } from "../types";

/** A customer-360 booking row — the same Booking shape, plus the display
 *  names CRMService.get_customer_360 resolves server-side. */
export type Customer360Booking = Booking & {
  vehicle_label?: string | null;
  vehicle_type_name?: string | null;
  service_names?: string[];
  address_text?: string | null;
  service_center_name?: string | null;
};

export type Customer360Subscription = UserSubscription & {
  plan_name?: string;
  /** Car type + the one wash a monthly pass covers. */
  vehicle_type_name?: string | null;
  service_name?: string | null;
};
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
  customerTypeahead: (q: string, limit?: number) =>
    apiClient.get<ApiSuccess<User[]>>("/crm/customers/typeahead", { params: { q, limit } }).then((r) => r.data.data),
  customer360: (id: string) => apiClient.get<ApiSuccess<Customer360>>(`/crm/customers/${id}`).then((r) => r.data.data),
};

/** The manager's "WhatsApp me new bookings" switch (on unless turned off).
 *  In-app alerts are unaffected. */
export interface NotificationPreferences {
  user_id: string;
  whatsapp_new_booking_alerts: boolean;
}

/** What happened to a staff → customer WhatsApp message. `status`: the
 *  send queue's state ("sent", "pending", "failed", "dead", "not_sent"…);
 *  `failure` the reason code when it didn't go. */
export interface UniversalMessageResult {
  customer_id: string;
  status: string;
  failure?: string | null;
  error?: string | null;
  channel?: string | null;
  template_name?: string | null;
}

export const staffMessagingApi = {
  /** Own settings (manager / admin). */
  preferences: () => apiClient.get<ApiSuccess<NotificationPreferences>>("/notifications/preferences").then((r) => r.data.data),
  /** Own switch, or — admin — a manager's (`userId`). */
  setNewBookingAlerts: (enabled: boolean, userId?: string) =>
    apiClient
      .put<ApiSuccess<NotificationPreferences>>("/notifications/preferences", { whatsapp_new_booking_alerts: enabled, user_id: userId || undefined })
      .then((r) => r.data.data),
  /** "Hi {name}, {message} — Team Blussit" through the approved template. */
  sendUniversalMessage: (customerId: string, message: string) =>
    apiClient
      .post<ApiSuccess<UniversalMessageResult>>("/notifications/universal-message", { customer_id: customerId, message })
      .then((r) => ({ result: r.data.data, message: r.data.message })),
};
