import { apiClient, type ApiPaginated, type ApiSuccess } from "../lib/api-client";
import type { Address, Booking, EquipmentUsed, Vehicle } from "../types";

/** What POST /bookings/group returns: the visit, and every car on it as a
 *  real booking of its own. */
export interface BookingGroupResult {
  booking_group_id: string;
  bookings: Booking[];
  vehicle_count: number;
  total_amount: number;
  /** How long the whole visit runs — all the cars' service time summed. */
  total_duration_minutes: number;
  service_center_id: string;
  scheduled_date: string;
  scheduled_slot: string;
  confirmation_token?: string;
}

export interface CreateBookingPayload {
  vehicle_id: string;
  address_id: string;
  service_ids?: string[];
  /** Per-unit add-on counts (service_id -> qty) — e.g. Extra Bike Wash ×3. */
  service_quantities?: Record<string, number>;
  combo_id?: string;
  scheduled_date: string;
  scheduled_slot: string;
  payment_method?: string;
  coupon_code?: string;
  subscription_id?: string;
  customer_notes?: string;
  hold_key?: string;
  alternate_contact_name?: string;
  alternate_contact_phone?: string;
}

export interface HeadingPayload {
  latitude: number;
  longitude: number;
  equipment_used?: EquipmentUsed[];
}

export interface PhotoCapturePayload {
  image_url: string;
  latitude: number;
  longitude: number;
}

export interface ManagerCreateBookingPayload {
  customer_id: string;
  vehicle_id?: string;
  new_vehicle?: Partial<Vehicle>;
  address_id?: string;
  new_address?: Partial<Address>;
  service_ids?: string[];
  service_quantities?: Record<string, number>;
  combo_id?: string;
  scheduled_date: string;
  scheduled_slot: string;
  payment_method?: string;
  coupon_code?: string;
  subscription_id?: string;
  customer_notes?: string;
  hold_key?: string;
  alternate_contact_name?: string;
  alternate_contact_phone?: string;
}

export interface EligibleCaptain {
  captain_id: string;
  full_name: string;
  eligible: boolean;
  reason?: string | null;
  // From the captain's last known GPS position — either a discrete capture
  // (heading-out, before/after photo) or, while they have an active job, a
  // periodic location ping (see the "captain-location:{id}" WebSocket
  // channel / staffDirectoryApi.pingLocation) refreshing it every ~25s.
  // null if they have no capture yet, or the booking's address has no
  // coordinates to compare against.
  distance_km?: number | null;
  latitude?: number | null;
  longitude?: number | null;
  last_location_at?: string | null;
  is_on_job?: boolean;
  current_job_count?: number;
  // Road distance/ETA (Routes API, traffic-aware) computed server-side for
  // located captains — the dispatcher-facing number; distance_km above is
  // the straight-line fallback.
  road_km?: number | null;
  eta_minutes?: number | null;
  eta_source?: string | null;
}

export const bookingApi = {
  create: (payload: CreateBookingPayload) => apiClient.post<ApiSuccess<Booking>>("/bookings", payload).then((r) => r.data.data),

  myBookings: (params?: { status?: string; page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<Booking>>("/bookings/my", { params }).then((r) => r.data),

  myJobs: (params?: { status?: string; page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<Booking>>("/bookings/my-jobs", { params }).then((r) => r.data),

  forCenter: (serviceCenterId: string, params?: { status?: string; page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<Booking>>(`/bookings/center/${serviceCenterId}`, { params }).then((r) => r.data),

  subscribersForCenter: (serviceCenterId: string) =>
    apiClient
      .get<
        ApiSuccess<
          { customer_id: string; customer_name: string; customer_phone?: string; plan_name: string; subscription_status?: string; remaining_service_count?: number; last_visit: string; visits: number }[]
        >
      >(`/bookings/center/${serviceCenterId}/subscribers`)
      .then((r) => r.data.data),

  all: (params?: { status?: string; service_center_id?: string; page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<Booking>>("/bookings", { params }).then((r) => r.data),

  get: (id: string) => apiClient.get<ApiSuccess<Booking>>(`/bookings/${id}`).then((r) => r.data.data),

  assignCaptain: (id: string, captainId: string) =>
    apiClient.post<ApiSuccess<Booking>>(`/bookings/${id}/assign-captain`, { captain_id: captainId }).then((r) => r.data.data),

  reassignCaptain: (id: string, captainId: string) =>
    apiClient.post<ApiSuccess<Booking>>(`/bookings/${id}/reassign-captain`, { captain_id: captainId }).then((r) => r.data.data),

  /** Every vehicle on one visit, enriched like a single booking. */
  getGroup: (groupId: string) =>
    apiClient.get<ApiSuccess<Booking[]>>(`/bookings/group/${groupId}`).then((r) => r.data.data),

  /** One decision for the whole visit. */
  switchGroupToCash: (groupId: string) =>
    apiClient.post<ApiSuccess<{ switched_count: number }>>(`/bookings/group/${groupId}/switch-to-cash`).then((r) => r.data.data),

  cancelGroup: (groupId: string, reason: string) =>
    apiClient.post<ApiSuccess<{ cancelled_count: number }>>(`/bookings/group/${groupId}/cancel`, { reason }).then((r) => r.data.data),

  /** Several of the customer's own vehicles washed on ONE visit: one
   *  address, one slot, one captain, one payment — and ONE slot seat,
   *  because it's a single trip. */
  createGroup: (payload: {
    vehicles: { vehicle_id: string; service_ids: string[]; service_quantities?: Record<string, number>; subscription_id?: string }[];
    address_id: string;
    scheduled_date: string;
    scheduled_slot: string;
    hold_key?: string;
    payment_method?: string;
    coupon_code?: string;
    customer_notes?: string;
    alternate_contact_name?: string;
    alternate_contact_phone?: string;
  }) =>
    apiClient
      .post<ApiSuccess<BookingGroupResult>>("/bookings/group", payload)
      .then((r) => r.data.data),

  /** "I couldn't finish paying online — let me pay the captain instead."
   *  Confirms a still-unpaid booking as a cash booking. */
  switchToCash: (id: string) =>
    apiClient.post<ApiSuccess<Booking>>(`/bookings/${id}/switch-to-cash`).then((r) => r.data.data),

  cancel: (id: string, reason: string) =>
    apiClient.post<ApiSuccess<Booking>>(`/bookings/${id}/cancel`, { reason }).then((r) => r.data.data),

  reschedule: (id: string, scheduled_date: string, scheduled_slot: string) =>
    apiClient.post<ApiSuccess<Booking>>(`/bookings/${id}/reschedule`, { scheduled_date, scheduled_slot }).then((r) => r.data.data),

  // Captain job-progression flow — geo-tagged & time-gated, mirrors backend/LOGIC_README.md
  captainCancel: (id: string, reason: string) =>
    apiClient.post<ApiSuccess<Booking>>(`/bookings/${id}/captain-cancel`, { reason }).then((r) => r.data.data),

  startHeading: (id: string, payload: HeadingPayload) =>
    apiClient.post<ApiSuccess<Booking>>(`/bookings/${id}/heading`, payload).then((r) => r.data.data),

  // "I've reached" + plate check in one press — carries the device GPS so
  // the backend can geofence-check the arrival like it does the photos.
  verifyVehicle: (id: string, registration_number: string, location?: { latitude: number; longitude: number; accuracy_m?: number }) =>
    apiClient.post<ApiSuccess<Booking>>(`/bookings/${id}/verify-vehicle`, { registration_number, ...location }).then((r) => r.data.data),

  resolveIssue: (id: string, note?: string) =>
    apiClient.post<ApiSuccess<Booking>>(`/bookings/${id}/resolve-issue`, { note }).then((r) => r.data.data),

  updatePriority: (id: string, priority: "high" | "medium" | "low") =>
    apiClient.patch<ApiSuccess<Booking>>(`/bookings/${id}/priority`, { priority }).then((r) => r.data.data),

  captureBeforePhoto: (id: string, payload: PhotoCapturePayload) =>
    apiClient.post<ApiSuccess<Booking>>(`/bookings/${id}/before-photo`, payload).then((r) => r.data.data),

  captureAfterPhoto: (id: string, payload: PhotoCapturePayload) =>
    apiClient.post<ApiSuccess<Booking>>(`/bookings/${id}/after-photo`, payload).then((r) => r.data.data),

  reportRisk: (id: string, note?: string) =>
    apiClient.post<ApiSuccess<Booking>>(`/bookings/${id}/report-risk`, { note }).then((r) => r.data.data),

  managerCreate: (payload: ManagerCreateBookingPayload) =>
    apiClient.post<ApiSuccess<Booking>>("/bookings/manager-create", payload).then((r) => r.data.data),

  eligibleCaptains: (id: string) =>
    apiClient.get<ApiSuccess<EligibleCaptain[]>>(`/bookings/${id}/eligible-captains`).then((r) => r.data.data),
};

export type TravelStatus = {
  status: string;
  store_to_customer: { km: number; minutes: number | null; source: string } | null;
  captain_to_customer: { km: number; minutes: number | null; source: string; captain_name: string | null; location_updated_at: string | null } | null;
};

export const travelStatusApi = {
  get: (bookingId: string) =>
    apiClient.get<ApiSuccess<TravelStatus>>(`/bookings/${bookingId}/travel-status`).then((r) => r.data.data),
};
