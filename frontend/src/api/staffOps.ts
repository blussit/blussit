import { apiClient, type ApiPaginated, type ApiSuccess } from "../lib/api-client";

export interface AttendanceRecord {
  id: string;
  captain_id: string;
  attendance_date: string;
  status: string;
  check_in_time?: string | null;
  check_out_time?: string | null;
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
  checkIn: (notes?: string) => apiClient.post<ApiSuccess<AttendanceRecord>>("/attendance/check-in", { notes }).then((r) => r.data.data),
  checkOut: () => apiClient.post<ApiSuccess<AttendanceRecord>>("/attendance/check-out").then((r) => r.data.data),
  mine: (params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<AttendanceRecord>>("/attendance/my", { params }).then((r) => r.data),
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
