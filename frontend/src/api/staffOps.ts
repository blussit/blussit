import { apiClient, type ApiPaginated, type ApiSuccess } from "../lib/api-client";

export interface AttendanceRecord {
  id: string;
  captain_id: string;
  attendance_date: string;
  status: string;
  check_in_time?: string | null;
  check_out_time?: string | null;
  check_in_location?: { latitude: number; longitude: number } | null;
  check_out_location?: { latitude: number; longitude: number } | null;
  worked_minutes?: number | null;
}

export interface GeoPayload {
  latitude?: number;
  longitude?: number;
  accuracy_m?: number;
}

export interface LeaveRequest {
  id: string;
  captain_id: string;
  start_date: string;
  end_date: string;
  reason: string;
  status: string;
}

export const attendanceApi = {
  checkIn: (payload?: GeoPayload & { notes?: string }) =>
    apiClient.post<ApiSuccess<AttendanceRecord>>("/attendance/check-in", payload || {}).then((r) => r.data.data),
  checkOut: (payload?: GeoPayload) =>
    apiClient.post<ApiSuccess<AttendanceRecord>>("/attendance/check-out", payload || {}).then((r) => r.data.data),
  mine: (params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<AttendanceRecord>>("/attendance/my", { params }).then((r) => r.data),
};

export interface CaptainKyc {
  status: "pending" | "submitted" | "verified" | "rejected";
  photo_url?: string | null;
  aadhaar_number?: string | null;
  pan_number?: string | null;
  aadhaar_doc_url?: string | null;
  pan_doc_url?: string | null;
  local_address?: string | null;
  permanent_address?: string | null;
  same_as_local?: boolean;
  submitted_at?: string | null;
  reviewed_at?: string | null;
  review_note?: string | null;
}

export const kycApi = {
  my: () => apiClient.get<ApiSuccess<CaptainKyc>>("/staff/my-kyc").then((r) => r.data.data),
  submit: (payload: Partial<CaptainKyc>) => apiClient.put<ApiSuccess<CaptainKyc>>("/staff/my-kyc", payload).then((r) => r.data.data),
  forCaptain: (captainId: string) => apiClient.get<ApiSuccess<CaptainKyc>>(`/staff/captains/${captainId}/kyc`).then((r) => r.data.data),
  review: (captainId: string, status: "verified" | "rejected", note?: string) =>
    apiClient.post<ApiSuccess<CaptainKyc>>(`/staff/captains/${captainId}/kyc/review`, { status, note }).then((r) => r.data.data),
};

export const leaveApi = {
  request: (payload: { start_date: string; end_date: string; reason: string }) =>
    apiClient.post<ApiSuccess<LeaveRequest>>("/leave-requests", payload).then((r) => r.data.data),
  mine: (params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<LeaveRequest>>("/leave-requests/my", { params }).then((r) => r.data),
  pendingForCenter: (serviceCenterId: string, params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<LeaveRequest>>(`/leave-requests/center/${serviceCenterId}`, { params }).then((r) => r.data),
  review: (id: string, status: string, review_note?: string) =>
    apiClient.put<ApiSuccess<LeaveRequest>>(`/leave-requests/${id}/review`, { status, review_note }).then((r) => r.data.data),
};
