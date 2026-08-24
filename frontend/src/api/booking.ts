import { apiClient, type ApiPaginated, type ApiSuccess } from "../lib/api-client";
import type { Address, Booking, EquipmentUsed, Vehicle } from "../types";

export interface CreateBookingPayload {
  vehicle_id: string;
  address_id: string;
  service_ids?: string[];
  combo_id?: string;
  scheduled_date: string;
  scheduled_slot: string;
  payment_method?: string;
  coupon_code?: string;
  subscription_id?: string;
  customer_notes?: string;
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
  combo_id?: string;
  scheduled_date: string;
  scheduled_slot: string;
  payment_method?: string;
  coupon_code?: string;
  subscription_id?: string;
  customer_notes?: string;
  alternate_contact_name?: string;
  alternate_contact_phone?: string;
}

export interface EligibleCaptain {
  captain_id: string;
  full_name: string;
  eligible: boolean;
  reason?: string | null;
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

  cancel: (id: string, reason: string) =>
    apiClient.post<ApiSuccess<Booking>>(`/bookings/${id}/cancel`, { reason }).then((r) => r.data.data),

  reschedule: (id: string, scheduled_date: string, scheduled_slot: string) =>
    apiClient.post<ApiSuccess<Booking>>(`/bookings/${id}/reschedule`, { scheduled_date, scheduled_slot }).then((r) => r.data.data),

  // Captain job-progression flow — geo-tagged & time-gated, mirrors backend/LOGIC_README.md
  captainCancel: (id: string, reason: string) =>
    apiClient.post<ApiSuccess<Booking>>(`/bookings/${id}/captain-cancel`, { reason }).then((r) => r.data.data),

  startHeading: (id: string, payload: HeadingPayload) =>
    apiClient.post<ApiSuccess<Booking>>(`/bookings/${id}/heading`, payload).then((r) => r.data.data),

  verifyVehicle: (id: string, registration_number: string) =>
    apiClient.post<ApiSuccess<Booking>>(`/bookings/${id}/verify-vehicle`, { registration_number }).then((r) => r.data.data),

  resolveIssue: (id: string, note?: string) =>
    apiClient.post<ApiSuccess<Booking>>(`/bookings/${id}/resolve-issue`, { note }).then((r) => r.data.data),

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
