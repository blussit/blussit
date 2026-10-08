/**
 * Captain on-site calls (spec 1.4 / 1.5, 2026-10-07): add services at the
 * door, and collect what the visit still owes at the end — cash (quoting
 * the amount his screen showed) or a Razorpay QR. Every amount here is the
 * server's `amount_due`, never a client sum.
 */
import { apiClient, type ApiSuccess } from "../lib/api-client";
import { catalogApi } from "./catalog";
import type { Booking } from "../types";

/** One line added on site (booking.added_services[]). */
export interface AddedServiceLine {
  service_id: string;
  name: string;
  qty: number;
  unit_price: number;
  amount: number;
  is_addon?: boolean;
  /** User id of whoever added it — compare with the captain's own id for "By You". */
  by?: string | null;
  by_name?: string | null;
  role?: string | null;
  at?: string | null;
  /** Booking status when it was added ("completed" = after the wash). */
  stage?: string | null;
  note?: string | null;
}

/** The booking money fields (spec 1.5) plus the edit / add-on markers —
 * read off any booking the captain loads. All optional: older rows and
 * not-yet-enriched responses may lack them. */
export interface OnsiteMoney {
  wallet_applied?: number | null;
  amount_paid?: number | null;
  amount_due?: number | null;
  wallet_due_carried?: number | null;
  added_services?: AddedServiceLine[] | null;
  added_services_total?: number | null;
  customer_edited_at?: string | null;
  /** Diff field names: services · vehicle · address · date · slot · notes · total. */
  customer_edited_fields?: string[] | null;
  customer_edited_by_role?: string | null;
}

export type OnsiteBooking = Booking & OnsiteMoney;

export interface AddServicesPayload {
  service_ids: string[];
  /** { service_id: n } — only per-unit add-ons go above 1. */
  quantities?: Record<string, number>;
  note?: string;
}

export interface AddServicesResult {
  booking: OnsiteBooking;
  added: AddedServiceLine[];
  added_total: number;
  /** This car's due after the add. */
  amount_due: number;
  /** The whole visit's due — what he collects at the end. */
  visit_amount_due: number;
}

export interface CollectState {
  /** "paid" once nothing on the visit is owed. */
  payment_status: string;
  payment_method?: string | null;
  /** Rupees still owed by the visit (status) or just collected (cash). */
  amount?: number;
  vehicles?: number;
  /** Cash only: the customer also paid online at the same moment — this much went to their wallet. */
  wallet_credit?: number;
}

export const captainOnsiteApi = {
  /** The catalogue (active services), shared cache with the website. */
  services: () => catalogApi.services({ page_size: 100 }).then((r) => r.data),
  /** POST /bookings/{id}/add-services — refusals come back as plain 400 messages. */
  addServices: (bookingId: string, payload: AddServicesPayload) =>
    apiClient.post<ApiSuccess<AddServicesResult>>(`/bookings/${bookingId}/add-services`, payload).then((r) => r.data.data),
  /** Cash for the visit's amount_due. `expectedAmount` = the amount shown;
   *  409 AMOUNT_DUE_CHANGED (details.amount_due) when it moved. */
  collectCash: (bookingId: string, expectedAmount: number) =>
    apiClient
      .post<ApiSuccess<CollectState>>("/payments/collect/cash", { booking_id: bookingId, expected_amount: expectedAmount })
      .then((r) => r.data.data),
  /** The QR's target: a Razorpay link for exactly what the visit owes (a pending one is reused). */
  collectLink: (bookingId: string) =>
    apiClient
      .post<ApiSuccess<{ short_url: string; amount: number; link_id?: string }>>("/payments/collect/link", { booking_id: bookingId })
      .then((r) => r.data.data),
  /** Live payment state for the visit (syncs pending links with Razorpay). */
  collectStatus: (bookingId: string) =>
    apiClient.get<ApiSuccess<CollectState>>(`/payments/collect/status/${bookingId}`).then((r) => r.data.data),
};
