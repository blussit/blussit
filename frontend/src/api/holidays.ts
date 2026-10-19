import { apiClient, type ApiPaginated, type ApiSuccess } from "../lib/api-client";

/** One holiday as the website popup shows it (GET /public/holidays). */
export interface PublicHoliday {
  id: string;
  scope: "all" | "centers";
  message: string;
  title: string | null;
  starts_at: string;
  ends_at: string;
  is_active_now: boolean;
  /** "1 Oct – 2 Oct" / "1 Oct" / "1 Oct, 2:00 PM – 6:00 PM" (IST). */
  dates_label: string;
  resume_at: string;
  /** "3 Oct 2026" or "3 Oct 2026, 2:00 PM". */
  resume_label: string;
  /** YYYY-MM-DD (IST) — the booking page opens on this day. */
  resume_date: string;
  /** "3 Oct" — for the "Book For 3 Oct" button. */
  resume_day: string;
}

export interface AffectedBookings {
  count: number;
  capped?: boolean;
  sample: {
    booking_id: string;
    booking_number: string;
    date: string | null;
    slot: string;
    center_name: string;
    customer_name: string;
    status: string;
  }[];
}

export interface AdminHoliday {
  id: string;
  title: string | null;
  message: string;
  scope: "all" | "centers";
  service_center_ids: string[];
  centers: { id: string; name: string }[];
  start_at: string;
  end_at: string;
  notice_from: string | null;
  start_date: string;
  start_time: string | null;
  end_date: string;
  end_time: string | null;
  notice_days_before: number;
  active: boolean;
  status: "upcoming" | "active" | "past";
  ended: "cancelled" | "ended_early" | null;
  original_end_at: string | null;
  dates_label: string;
  resume_at: string;
  resume_label: string;
  resume_date: string;
  popup: PublicHoliday;
  created_at: string | null;
  cancelled_at: string | null;
}

export interface HolidayInput {
  title?: string | null;
  message: string;
  scope: "all" | "centers";
  service_center_ids: string[];
  start_date: string;
  start_time?: string | null;
  end_date: string;
  end_time?: string | null;
  notice_days_before: number;
}

export interface HolidaySaveResult {
  holiday: AdminHoliday;
  affected_bookings: AffectedBookings;
}

export const holidayApi = {
  /** Holidays whose popup is showing now — all-centers ones, plus that center's. */
  publicList: (centerId?: string | null) =>
    apiClient
      .get<ApiSuccess<PublicHoliday[]>>("/public/holidays", { params: centerId ? { center_id: centerId } : undefined })
      .then((r) => r.data.data),
  /** The signed-in customer's center's holidays. */
  mine: () => apiClient.get<ApiSuccess<PublicHoliday[]>>("/holidays/mine").then((r) => r.data.data),
};

export const adminHolidayApi = {
  list: (params: { status: "upcoming" | "active" | "past" | "all"; page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<AdminHoliday>>("/admin/holidays", { params }).then((r) => r.data),
  preview: (payload: HolidayInput, holidayId?: string | null) =>
    apiClient
      .post<ApiSuccess<HolidaySaveResult>>("/admin/holidays/preview", payload, { params: holidayId ? { holiday_id: holidayId } : undefined })
      .then((r) => r.data.data),
  create: (payload: HolidayInput) => apiClient.post<ApiSuccess<HolidaySaveResult>>("/admin/holidays", payload).then((r) => r.data.data),
  update: (id: string, payload: Partial<HolidayInput>) =>
    apiClient.patch<ApiSuccess<HolidaySaveResult>>(`/admin/holidays/${id}`, payload).then((r) => r.data.data),
  end: (id: string) => apiClient.post<ApiSuccess<{ holiday: AdminHoliday }>>(`/admin/holidays/${id}/end`).then((r) => r.data.data),
};
