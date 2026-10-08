import { API_BASE_URL, apiClient, UPLOAD_TIMEOUT_MS, type ApiPaginated, type ApiSuccess } from "../lib/api-client";
import type { AttendanceRecord } from "./staffOps";
import type { BookingPolicy, CapacityPolicyChange, CapacityPolicyOverview, Category, ComboOffer, ContactMessage, Coupon, DailyCapacitySummary, HomepageConfig, InventoryItem, PlanEnquiry, PricingConfig, Service, ServiceCenter, SlotCapacityDetail, SubscriptionPlan, User, VehicleTypeOption, VisitorStats } from "../types";

export const adminPricingApi = {
  get: () => apiClient.get<ApiSuccess<PricingConfig>>("/pricing-config").then((r) => r.data.data),
  set: (payload: Omit<PricingConfig, "updated_at">) => apiClient.put<ApiSuccess<PricingConfig>>("/pricing-config", payload).then((r) => r.data.data),
};

export const adminBookingPolicyApi = {
  get: () => apiClient.get<ApiSuccess<BookingPolicy>>("/booking-policy").then((r) => r.data.data),
  set: (payload: Partial<BookingPolicy>) => apiClient.put<ApiSuccess<BookingPolicy>>("/booking-policy", payload).then((r) => r.data.data),
};

/** One saved change to an admin setting: who, when, and each field's before → after. */
export interface SettingsChange {
  changed_at: string;
  changed_by?: string | null;
  changed_by_name: string;
  changes: Record<string, { from: unknown; to: unknown }>;
}

export const adminSettingsApi = {
  history: (key: string) => apiClient.get<ApiSuccess<SettingsChange[]>>(`/settings-history/${key}`).then((r) => r.data.data),
};

export const adminHomepageConfigApi = {
  get: () => apiClient.get<ApiSuccess<HomepageConfig>>("/homepage-config").then((r) => r.data.data),
  set: (payload: Partial<HomepageConfig>) => apiClient.put<ApiSuccess<HomepageConfig>>("/homepage-config", payload).then((r) => r.data.data),
};

export const adminContactMessageApi = {
  list: (params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<ContactMessage>>("/contact", { params }).then((r) => r.data),
};

export const adminPlanEnquiryApi = {
  list: (params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<PlanEnquiry>>("/subscriptions/enquiries", { params }).then((r) => r.data),
  setStatus: (id: string, status: PlanEnquiry["status"]) =>
    apiClient.post(`/subscriptions/enquiries/${id}/status`, null, { params: { status } }),
};

export const adminComboOfferApi = {
  list: () => apiClient.get<ApiSuccess<ComboOffer[]>>("/combo-offers").then((r) => r.data.data),
  create: (payload: { [K in keyof ComboOffer]?: ComboOffer[K] | null }) => apiClient.post<ApiSuccess<ComboOffer>>("/combo-offers", payload).then((r) => r.data.data),
  update: (id: string, payload: { [K in keyof ComboOffer]?: ComboOffer[K] | null }) => apiClient.put<ApiSuccess<ComboOffer>>(`/combo-offers/${id}`, payload).then((r) => r.data.data),
  remove: (id: string) => apiClient.delete(`/combo-offers/${id}`).then((r) => r.data),
};

export const adminUserApi = {
  list: (params?: { role?: string; page?: number; page_size?: number; search?: string; period?: string; start?: string; end?: string }) =>
    apiClient.get<ApiPaginated<User>>("/users", { params }).then((r) => r.data),
  get: (id: string) => apiClient.get<ApiSuccess<User>>(`/users/${id}`).then((r) => r.data.data),
  update: (id: string, payload: Partial<User>) => apiClient.put<ApiSuccess<User>>(`/users/${id}`, payload).then((r) => r.data.data),
  suspend: (id: string) => apiClient.post<ApiSuccess<User>>(`/users/${id}/suspend`).then((r) => r.data.data),
  remove: (id: string) => apiClient.delete(`/users/${id}`).then((r) => r.data),
  createStaff: (payload: { full_name: string; email?: string; phone?: string; password: string; role: string; service_center_id?: string; photo_url?: string }) =>
    apiClient.post<ApiSuccess<User>>("/auth/staff", payload).then((r) => r.data.data),
  // Manager/admin creating a customer on their behalf (e.g. a phone/walk-in
  // booking) — the account gets a temp password the customer must change on
  // first login, see User.must_change_password.
  createCustomer: (payload: { full_name: string; phone: string; email?: string; temp_password: string }) =>
    apiClient.post<ApiSuccess<User>>("/auth/customers", payload).then((r) => r.data.data),
};

export const adminServiceCenterApi = {
  list: (params?: { page?: number; page_size?: number; search?: string; active_only?: boolean }) =>
    apiClient.get<ApiPaginated<ServiceCenter>>("/service-centers", { params }).then((r) => r.data),
  get: (id: string) => apiClient.get<ApiSuccess<ServiceCenter>>(`/service-centers/${id}`).then((r) => r.data.data),
  create: (payload: { [K in keyof ServiceCenter]?: ServiceCenter[K] | null }) =>
    apiClient.post<ApiSuccess<ServiceCenter>>("/service-centers", payload).then((r) => r.data.data),
  update: (id: string, payload: { [K in keyof ServiceCenter]?: ServiceCenter[K] | null }) =>
    apiClient.put<ApiSuccess<ServiceCenter>>(`/service-centers/${id}`, payload).then((r) => r.data.data),
  remove: (id: string) => apiClient.delete(`/service-centers/${id}`).then((r) => r.data),
};

export const adminSlotCapacityApi = {
  get: (centerId: string, date: string) =>
    apiClient
      .get<ApiSuccess<{ slots: SlotCapacityDetail[]; daily: DailyCapacitySummary | null }>>(`/service-centers/${centerId}/slot-capacity`, { params: { date } })
      .then((r) => r.data.data),
  set: (centerId: string, payload: { date: string; slot_key: string; capacity?: number; is_closed?: boolean }) =>
    apiClient.put<ApiSuccess<unknown>>(`/service-centers/${centerId}/slot-capacity`, payload).then((r) => r.data.data),
};

// Effective-dated capacity policy — the single source of truth for a
// center's daily maximum + per-slot distribution (see
// CapacityPolicyService on the backend). adminSlotCapacityApi above stays
// for per-date/per-slot OVERRIDES and the actual booked/remaining
// counters; this is for the underlying baseline policy those overrides
// sit on top of.
export const adminCapacityPolicyApi = {
  overview: (centerId: string) => apiClient.get<ApiSuccess<CapacityPolicyOverview>>(`/service-centers/${centerId}/capacity-policy`).then((r) => r.data.data),
  history: (centerId: string) => apiClient.get<ApiSuccess<CapacityPolicyChange[]>>(`/service-centers/${centerId}/capacity-policy/history`).then((r) => r.data.data),
  schedule: (centerId: string, payload: { effective_date: string; max_bookings_per_day: number; slot_distribution?: Record<string, number> | null; note?: string }) =>
    apiClient.post<ApiSuccess<CapacityPolicyChange>>(`/service-centers/${centerId}/capacity-policy`, payload).then((r) => r.data.data),
  cancel: (centerId: string, changeId: string) => apiClient.delete(`/service-centers/${centerId}/capacity-policy/${changeId}`).then((r) => r.data),
};

export const adminVehicleTypeApi = {
  create: (payload: { name: string; display_order?: number; is_active?: boolean }) =>
    apiClient.post<ApiSuccess<VehicleTypeOption>>("/vehicle-types", payload).then((r) => r.data.data),
  update: (id: string, payload: Partial<{ name: string; display_order: number; is_active: boolean }>) =>
    apiClient.put<ApiSuccess<VehicleTypeOption>>(`/vehicle-types/${id}`, payload).then((r) => r.data.data),
  remove: (id: string) => apiClient.delete(`/vehicle-types/${id}`).then((r) => r.data),
};

type ServicePayload = { [K in keyof Service]?: Service[K] | null };

export const adminCatalogApi = {
  createCategory: (payload: Partial<Category>) => apiClient.post<ApiSuccess<Category>>("/categories", payload).then((r) => r.data.data),
  updateCategory: (id: string, payload: Partial<Category>) =>
    apiClient.put<ApiSuccess<Category>>(`/categories/${id}`, payload).then((r) => r.data.data),
  deleteCategory: (id: string) => apiClient.delete(`/categories/${id}`).then((r) => r.data),

  // `null` is meaningful on update (clears an optional price / variant), so
  // the payload is any subset of Service's keys with nullable values.
  createService: (payload: ServicePayload) => apiClient.post<ApiSuccess<Service>>("/services", payload).then((r) => r.data.data),
  updateService: (id: string, payload: ServicePayload) =>
    apiClient.put<ApiSuccess<Service>>(`/services/${id}`, payload).then((r) => r.data.data),
  deleteService: (id: string) => apiClient.delete(`/services/${id}`).then((r) => r.data),
};

export const adminSubscriptionPlanApi = {
  create: (payload: Partial<SubscriptionPlan>) => apiClient.post<ApiSuccess<SubscriptionPlan>>("/subscription-plans", payload).then((r) => r.data.data),
  update: (id: string, payload: Partial<SubscriptionPlan>) =>
    apiClient.put<ApiSuccess<SubscriptionPlan>>(`/subscription-plans/${id}`, payload).then((r) => r.data.data),
  remove: (id: string) => apiClient.delete(`/subscription-plans/${id}`).then((r) => r.data),
};

export const adminCouponApi = {
  list: (params?: { page?: number; page_size?: number; active_only?: boolean }) =>
    apiClient.get<ApiPaginated<Coupon>>("/coupons", { params }).then((r) => r.data),
  create: (payload: { [K in keyof Coupon]?: Coupon[K] | null }) => apiClient.post<ApiSuccess<Coupon>>("/coupons", payload).then((r) => r.data.data),
  update: (id: string, payload: { [K in keyof Coupon]?: Coupon[K] | null }) => apiClient.put<ApiSuccess<Coupon>>(`/coupons/${id}`, payload).then((r) => r.data.data),
  remove: (id: string) => apiClient.delete(`/coupons/${id}`).then((r) => r.data),
};

export const inventoryApi = {
  forCenter: (serviceCenterId: string, params?: { page?: number; page_size?: number; low_stock_only?: boolean }) =>
    apiClient.get<ApiPaginated<InventoryItem>>(`/inventory/center/${serviceCenterId}`, { params }).then((r) => r.data),
  create: (payload: Partial<InventoryItem>) => apiClient.post<ApiSuccess<InventoryItem>>("/inventory", payload).then((r) => r.data.data),
  update: (id: string, payload: Partial<InventoryItem>) =>
    apiClient.put<ApiSuccess<InventoryItem>>(`/inventory/${id}`, payload).then((r) => r.data.data),
  adjust: (id: string, delta: number, reason?: string) =>
    apiClient.post<ApiSuccess<InventoryItem>>(`/inventory/${id}/adjust`, { delta, reason }).then((r) => r.data.data),
  remove: (id: string) => apiClient.delete(`/inventory/${id}`).then((r) => r.data),
};

export interface CaptainPerformance {
  booking_status_counts: Record<string, number>;
  total_bookings: number;
  cancelled_bookings: number;
  reassigned_away_count: number;
  average_rating: number;
  total_reviews: number;
  repeat_complaints: number;
  total_jobs_completed: number;
  total_earnings: number;
  avg_heading_punctuality_minutes: number | null;
  on_time_start_pct: number | null;
  on_time_completion_pct: number | null;
  delayed_jobs: number;
  avg_delay_minutes: number | null;
  avg_travel_minutes: number | null;
  avg_waiting_minutes: number | null;
  avg_service_minutes: number | null;
  avg_total_job_minutes: number | null;
  jobs_per_day: number | null;
  jobs_per_slot: number | null;
}

export const staffDirectoryApi = {
  /** Suspend / reactivate one of the manager's own captains (manager or admin). */
  setCaptainStatus: (captainId: string, status: "active" | "suspended") =>
    apiClient.post<ApiSuccess<User>>(`/staff/captains/${captainId}/status`, { status }).then((r) => r.data.data),
  captainsForCenter: (serviceCenterId: string, params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<User>>(`/staff/captains/center/${serviceCenterId}`, { params }).then((r) => r.data),
  captainPerformance: (captainId: string, params?: { date_from?: string; date_to?: string; service_center_id?: string }) =>
    apiClient.get<ApiSuccess<CaptainPerformance>>(`/staff/captains/${captainId}/performance`, { params }).then((r) => r.data.data),
  myPerformance: () => apiClient.get<ApiSuccess<CaptainPerformance>>("/staff/my-performance").then((r) => r.data.data),
  performanceForCenter: (serviceCenterId: string, params?: { date_from?: string; date_to?: string }) =>
    apiClient
      .get<ApiSuccess<(CaptainPerformance & { captain_id: string; full_name: string })[]>>(`/staff/captains/center/${serviceCenterId}/performance`, { params })
      .then((r) => r.data.data),
  // Captain-side location ping while an active job is in progress — see
  // BookingService.update_captain_location/has_active_job. Rejected with a
  // normal error if the captain has no active job right now, which callers
  // should just swallow (this is a background sender, not a user action).
  pingLocation: (latitude: number, longitude: number) => apiClient.post<ApiSuccess<null>>("/staff/captains/location", { latitude, longitude }).then((r) => r.data.data),
  attendance: (captainId: string, params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<AttendanceRecord>>(`/staff/captains/${captainId}/attendance`, { params }).then((r) => r.data),
  // GPS breadcrumb trail (30-day TTL) for the manager map/audit view.
  locationTrail: (captainId: string, since?: string) =>
    apiClient
      .get<ApiSuccess<{ latitude: number; longitude: number; source: string; booking_id?: string | null; at: string }[]>>(
        `/staff/captains/${captainId}/locations`,
        { params: since ? { since } : undefined }
      )
      .then((r) => r.data.data),
};

export interface ServiceCenterSummary {
  service_center_id: string;
  name: string;
  bookings: number;
  completed: number;
  pending: number;
  delayed: number;
  avg_rating: number | null;
  review_count: number;
}

export const analyticsApi = {
  dashboard: () => apiClient.get<ApiSuccess<Record<string, unknown>>>("/analytics/dashboard").then((r) => r.data.data),
  bookingTrends: (days = 30) =>
    apiClient
      .get<ApiSuccess<{ date: string; bookings: number; revenue: number }[]>>("/analytics/booking-trends", { params: { days } })
      .then((r) => r.data.data),
  serviceCenterSummaries: () => apiClient.get<ApiSuccess<ServiceCenterSummary[]>>("/analytics/service-centers").then((r) => r.data.data),
  managerSummary: (serviceCenterId: string) => apiClient.get<ApiSuccess<Record<string, number | null>>>(`/analytics/manager-summary/${serviceCenterId}`).then((r) => r.data.data),
  vehicleTypeBreakdown: (serviceCenterId?: string) =>
    apiClient.get<ApiSuccess<VehicleTypeKpi[]>>("/analytics/vehicle-types", { params: serviceCenterId ? { service_center_id: serviceCenterId } : undefined }).then((r) => r.data.data),
  serviceBreakdown: (serviceCenterId?: string) =>
    apiClient.get<ApiSuccess<ServiceKpi[]>>("/analytics/services", { params: serviceCenterId ? { service_center_id: serviceCenterId } : undefined }).then((r) => r.data.data),
  /** Website visitors — one count per device per IST day. Same period params as /analytics/kpis/*. */
  visitors: (params: { period?: string; start?: string; end?: string }) =>
    apiClient.get<ApiSuccess<VisitorStats>>("/analytics/visitors", { params }).then((r) => r.data.data),
};

export interface VehicleTypeKpi {
  vehicle_type_id: string;
  vehicle_type_name: string;
  total_bookings: number;
  completed_bookings: number;
  avg_service_minutes: number | null;
  avg_travel_minutes: number | null;
  avg_total_minutes: number | null;
  delayed_count: number;
  avg_rating: number | null;
}

export interface ServiceKpi {
  service_id: string;
  service_name: string;
  total_bookings: number;
  completed_bookings: number;
  avg_actual_minutes: number | null;
  avg_expected_minutes: number | null;
  avg_travel_minutes: number | null;
  avg_total_minutes: number | null;
  delayed_count: number;
  avg_rating: number | null;
}

export const crmApi = {
  customer360: (customerId: string) => apiClient.get<ApiSuccess<Record<string, unknown>>>(`/crm/customers/${customerId}`).then((r) => r.data.data),
};

export interface CoverageLead {
  id: string;
  name: string;
  phone: string;
  pincode: string;
  city_area?: string | null;
  service_interest?: string | null;
  requests_count: number;
  last_requested_at: string;
  created_at: string;
}

export const coverageLeadApi = {
  list: (params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<CoverageLead>>("/coverage-leads", { params }).then((r) => r.data),
  summary: () =>
    apiClient
      .get<ApiSuccess<{ total_people: number; top_pincodes: { pincode: string; people: number; requests: number }[] }>>("/coverage-leads/summary")
      .then((r) => r.data.data),
};

export interface AuditLogRow {
  id: string;
  actor_id: string;
  actor_role: string;
  actor_name?: string | null;
  action: string;
  module: string;
  target_id?: string | null;
  target_label?: string | null;
  service_center_id?: string | null;
  service_center_name?: string | null;
  /** An admin acted on a center-owned record (e.g. working its queue). */
  admin_in_center?: boolean;
  details?: Record<string, unknown>;
  created_at: string;
}

export const auditLogApi = {
  list: (params?: {
    module?: string; actor_id?: string; actor_role?: string; service_center_id?: string; admin_in_center?: boolean;
    page?: number; page_size?: number;
  }) => apiClient.get<ApiPaginated<AuditLogRow>>("/audit-logs", { params }).then((r) => r.data),
};

// ---- Management KPI engine (admin dashboard analytics tabs) --------------
export type KpiPeriodParams = { period?: string; start?: string; end?: string };

export interface ManagerDashboard {
  center: { id: string; name: string | null };
  period: { start: string; end: string };
  sales: { current: ManagerOverviewBlock; previous: ManagerOverviewBlock };
  services: { service_id: string; name: string; is_addon: boolean; washes: number; bookings: number; revenue: number }[];
  vehicle_types: { vehicle_type_id: string | null; name: string; washes: number; revenue: number }[];
  washes: { washes: number; revenue: number; plan_washes: number };
  plans: { items: { plan_id: string; name: string; sold: number; revenue: number }[]; sold: number; revenue: number };
  today: {
    date: string;
    slots: {
      key: string; label: string; cars: number; visits: number; capacity: number | null;
      /** false = no capacity set for this slot: it takes NO bookings. */
      capacity_configured?: boolean;
      unassigned: number; in_progress: number; completed: number; is_closed: boolean;
    }[];
    cars: number; visits: number; capacity: number | null; load_pct: number | null;
    unassigned: number; in_progress: number; completed: number;
  };
  captains: {
    items: {
      id: string; name: string; phone?: string | null; photo_url?: string | null;
      state: "on_job" | "available" | "on_leave" | "checked_out" | "not_checked_in";
      current_booking: string | null; jobs_today: number; done_today: number; checked_in_at?: string | null;
    }[];
    counts: { total: number; available: number; on_job: number; on_leave: number; off_duty: number };
  };
  ops: {
    needs_captain: number; late_starts: number; open_issues: number; open_complaints: number; low_stock: number;
    issues: {
      id: string; booking_number: string; issue_flag: string; scheduled_date: string | null; scheduled_slot: string; slot_label: string;
      vehicle_type_name?: string | null; service_names?: string[];
    }[];
  };
}

export interface ManagerOverviewBlock {
  bookings: number;
  completed: number;
  revenue: number;
  plans_sold: number;
  plan_revenue: number;
  combined_revenue: number;
}

/** Filters the dashboard's interactive charts slice by — every chart,
 *  total and drill-down list on the Explore panel uses the same set. */
export type KpiExplorerFilters = KpiPeriodParams & {
  service_center_id?: string;
  service_id?: string;
  vehicle_type?: string;
  source?: string;
  granularity?: "auto" | "day" | "week" | "month";
};

export interface KpiExplorerTotals {
  bookings: number;
  completed: number;
  cancelled: number;
  revenue: number;
  aov: number;
  completion_rate: number | null;
  plans_sold: number;
  plan_revenue: number;
  combined_revenue: number;
}

export interface KpiExplorerBucket {
  key: string;
  /** Inclusive IST dates this bucket covers (already clipped to the range). */
  start: string;
  end: string;
  bookings: number;
  completed: number;
  cancelled: number;
  revenue: number;
  plans_sold: number;
  plan_revenue: number;
}

export interface KpiExplorerData {
  range: { start: string; end: string; granularity: "day" | "week" | "month" };
  totals: KpiExplorerTotals;
  previous: KpiExplorerTotals;
  series: KpiExplorerBucket[];
  by_service: { id: string; name: string; bookings: number; completed: number; revenue: number }[];
  by_vehicle_type: { id: string | null; name: string; bookings: number; revenue: number }[];
  by_center: { id: string | null; name: string; bookings: number; revenue: number }[];
  by_source: { key: string; name: string; bookings: number; revenue: number }[];
  by_plan: { id: string | null; name: string; sold: number; revenue: number }[];
  options: {
    centers: { id: string; name: string }[];
    services: { id: string; name: string }[];
    vehicle_types: { id: string; name: string }[];
    sources: { key: string; name: string }[];
  };
}

export const kpiApi = {
  section: <T = Record<string, unknown>>(section: string, params: KpiPeriodParams) =>
    apiClient.get<ApiSuccess<T>>(`/analytics/kpis/${section}`, { params }).then((r) => r.data.data),
  explorer: (params: KpiExplorerFilters) =>
    apiClient.get<ApiSuccess<KpiExplorerData>>("/analytics/kpis-explorer", { params }).then((r) => r.data.data),
  /** A manager's own combined bookings+plans revenue for their center —
   *  the Sales numbers on the manager Dashboard. */
  /** The manager's single home screen — sales, washes per service / car
   *  type, plans sold, today's slot load, captains, alarms. One center. */
  managerDashboard: (serviceCenterId: string, params: KpiPeriodParams) =>
    apiClient.get<ApiSuccess<ManagerDashboard>>(`/analytics/manager-dashboard/${serviceCenterId}`, { params }).then((r) => r.data.data),
  managerOverview: (serviceCenterId: string, params: KpiPeriodParams) =>
    apiClient
      .get<ApiSuccess<{ current: ManagerOverviewBlock; previous: ManagerOverviewBlock }>>(`/analytics/kpis/manager-overview/${serviceCenterId}`, { params })
      .then((r) => r.data.data),
  getSettings: () => apiClient.get<ApiSuccess<BusinessSettings>>("/analytics/business-settings").then((r) => r.data.data),
  updateSettings: (payload: Partial<BusinessSettings>) =>
    apiClient.put<ApiSuccess<BusinessSettings>>("/analytics/business-settings", payload).then((r) => r.data.data),
};

export type MarketingEntry = {
  date: string;
  source: string;
  campaign?: string;
  spend: number;
  leads?: number;
  customers?: number;
  revenue?: number;
};

export type BusinessSettings = {
  variable_cost_per_wash: number;
  fixed_cost_monthly: number;
  kit_cost: number;
  kits_count: number;
  targets: {
    washes_per_captain_per_day: number;
    repeat_rate_pct: number;
    capacity_utilisation_pct: number;
    avg_rating: number;
    cac: number;
  };
  marketing_entries: MarketingEntry[];
};

// ---- WhatsApp CRM ---------------------------------------------------------
export type WaConversation = {
  wa_id: string; phone: string; name: string; customer_id: string | null;
  last_message_text: string | null; last_message_at: string | null; last_message_direction: "in" | "out" | null;
  unread_count: number; crm_status: "open" | "pending" | "resolved";
  assigned_to: string | null; assigned_to_name: string | null;
  tags: string[]; bot_paused: boolean;
  window: { active: boolean; expires_at: string | null };
  has_active_booking: boolean;
};

export type WaMessage = {
  id: string; direction: "in" | "out"; type: string; text: string;
  media_type?: string | null; media_id?: string | null; mime_type?: string | null; filename?: string | null;
  latitude?: number | null; longitude?: number | null; template_name?: string | null;
  at: string | null; status: "SENT" | "DELIVERED" | "READ" | "FAILED" | null;
  errors?: { code: number; title: string; message: string }[] | null;
  automated: boolean; sender: string;
};

export type WaTemplate = {
  name: string; status: string; category: string | null; language: string | null;
  body: string; param_count: number; rejected_reason: string | null;
  disabled: boolean; usage: number; synced_at: string | null;
};

export type WaContactProfile = {
  conversation: WaConversation;
  customer: { id: string; name: string; phone: string; email: string | null; status: string; created_at: string | null; phone_verified: boolean } | null;
  vehicles: { brand: string | null; model: string | null; registration_number: string | null; type: string | null }[];
  current_booking: { booking_number: string; id: string; services: string[]; date: string; slot: string; status: string; captain: string | null; amount: number } | null;
  stats: { total_bookings: number; completed: number; cancelled: number; last_service: string | null; lifetime_value: number; avg_rating_given: number | null; is_repeat: boolean } | null;
};

/** Why WhatsApp would NOT reach people (managers who can't get alerts,
 *  unapproved templates, Meta refusing our credentials…). */
export interface WaConfigWarning {
  severity: "error" | "warning" | "info";
  code: string;
  message: string;
  user_id?: string | null;
  name?: string | null;
  service_center_id?: string | null;
}

/** One template the app uses (Admin → WhatsApp → Templates → Submit). */
export interface WaCatalogueTemplate {
  key: string;
  name: string;
  /** The newest version on Meta (name_v2…), if any. */
  live_name?: string | null;
  /** Meta status of the newest version, or NOT_SUBMITTED. */
  status: string;
  meta_category?: string | null;
  rejected_reason?: string | null;
  can_submit: boolean;
  /** The name Submit would use (the next version after a rejection). */
  submit_name?: string | null;
  category: string;
  body: string;
  examples?: string[] | null;
  button_text?: string | null;
  button_url?: string | null;
  events?: string[] | null;
  note?: string | null;
}

export interface WaSettings {
  google_review_url: string;
}

export interface WaDeliveryHealth {
  days: number;
  config_warnings?: WaConfigWarning[];
  /** Queued sends by status: sending (first try in flight), pending (waiting
   *  to retry), sent, failed (refused for good), dead (gave up after
   *  retries), undelivered (accepted by Meta, then not delivered). */
  queue: Record<string, number>;
  failures_by_reason: Record<string, number>;
  /** Free text refused outside the 24-hour window / no template. */
  refused_outside_window: number;
  recent_failures: {
    id: string;
    user_id?: string | null;
    phone?: string | null;
    title?: string | null;
    event?: string | null;
    status?: string | null;
    failure?: string | null;
    error?: string | null;
    attempts?: number | null;
    at?: string | null;
  }[];
}

export const whatsappCrmApi = {
  /** Newest first; pass the last row's `last_message_at` as `before` for the next (older) page. */
  conversations: (filter = "all", search = "", before?: string | null, limit?: number) =>
    apiClient
      .get<ApiSuccess<WaConversation[]>>("/whatsapp/crm/conversations", { params: { filter, search, before: before || undefined, limit } })
      .then((r) => r.data.data),
  thread: (waId: string) =>
    apiClient.get<ApiSuccess<{ conversation: WaConversation; messages: WaMessage[] }>>(`/whatsapp/crm/conversations/${waId}/messages`).then((r) => r.data.data),
  markRead: (waId: string) => apiClient.post(`/whatsapp/crm/conversations/${waId}/read`),
  sendText: (waId: string, text: string) => apiClient.post(`/whatsapp/crm/conversations/${waId}/send`, { text }),
  sendTemplate: (waId: string, template_name: string, params: string[]) =>
    apiClient.post(`/whatsapp/crm/conversations/${waId}/send-template`, { template_name, params }),
  sendMedia: (waId: string, payload: { media_type: string; media_id: string; caption: string; filename: string }) =>
    apiClient.post(`/whatsapp/crm/conversations/${waId}/send-media`, payload),
  startConversation: (phone: string, template_name: string, params: string[]) =>
    apiClient.post<ApiSuccess<{ wa_id: string }>>("/whatsapp/crm/conversations/start", { phone, template_name, params }).then((r) => r.data.data),
  assign: (waId: string, user_id: string | null) => apiClient.post(`/whatsapp/crm/conversations/${waId}/assign`, { user_id }),
  setStatus: (waId: string, status: string) => apiClient.post(`/whatsapp/crm/conversations/${waId}/status`, { status }),
  setTags: (waId: string, tags: string[]) => apiClient.post(`/whatsapp/crm/conversations/${waId}/tags`, { tags }),
  setBotPaused: (waId: string, paused: boolean) => apiClient.post(`/whatsapp/crm/conversations/${waId}/bot`, { paused }),
  contactProfile: (waId: string) => apiClient.get<ApiSuccess<WaContactProfile>>(`/whatsapp/crm/contacts/${waId}`).then((r) => r.data.data),
  contacts: (search = "", before?: string | null, limit?: number) =>
    apiClient
      .get<ApiSuccess<WaConversation[]>>("/whatsapp/crm/contacts", { params: { search, before: before || undefined, limit } })
      .then((r) => r.data.data),
  agents: () => apiClient.get<ApiSuccess<{ id: string; name: string; role: string }[]>>("/whatsapp/crm/agents").then((r) => r.data.data),
  badge: () => apiClient.get<ApiSuccess<{ unread_conversations: number }>>("/whatsapp/crm/badge").then((r) => r.data.data),
  /** What did NOT reach people on WhatsApp in the last `days`. */
  // `fresh`: skip the server's 15-second cache (the card's Refresh button).
  deliveryHealth: (days = 7, fresh = false) =>
    apiClient
      .get<ApiSuccess<WaDeliveryHealth>>("/whatsapp/crm/delivery-health", { params: { days, ...(fresh ? { fresh: 1 } : {}) } })
      .then((r) => r.data.data),
  defaultTags: () => apiClient.get<ApiSuccess<{ tags: string[] }>>("/whatsapp/crm/tags").then((r) => r.data.data),
  analytics: (days = 30) => apiClient.get<ApiSuccess<Record<string, never> & Record<string, unknown>>>("/whatsapp/crm/analytics", { params: { days } }).then((r) => r.data.data),
  templates: (params?: { sendable?: boolean }) =>
    apiClient.get<ApiSuccess<WaTemplate[]>>("/whatsapp/crm/templates", { params }).then((r) => r.data.data),
  syncTemplates: () => apiClient.post<ApiSuccess<{ synced: number }>>("/whatsapp/crm/templates/sync").then((r) => r.data.data),
  // Submits every standard BLUSSIT template that doesn't exist on the
  // WABA yet (including the booking-deep-link ones) — safe to click any
  // time; anything already submitted/approved is left untouched.
  bootstrapTemplates: () => apiClient.post<ApiSuccess<{ name: string; status: string; note?: string }[]>>("/whatsapp/crm/templates/bootstrap").then((r) => r.data.data),
  createTemplate: (payload: { name: string; category: string; language: string; body: string; button_text?: string; button_url?: string }) =>
    apiClient.post("/whatsapp/crm/templates", payload),
  setTemplateDisabled: (name: string, paused: boolean) => apiClient.patch(`/whatsapp/crm/templates/${name}/disabled`, { paused }),
  /** Every template the app uses, with Meta's status and what Submit would send. */
  templateCatalogue: () => apiClient.get<ApiSuccess<WaCatalogueTemplate[]>>("/whatsapp/crm/templates/catalogue").then((r) => r.data.data),
  submitCatalogueTemplate: (key: string) =>
    apiClient
      .post<ApiSuccess<{ name: string; status: string; note?: string }>>(`/whatsapp/crm/templates/catalogue/${encodeURIComponent(key)}/submit`)
      .then((r) => r.data.data),
  settings: () => apiClient.get<ApiSuccess<WaSettings>>("/whatsapp/crm/settings").then((r) => r.data.data),
  /** google_review_url: https:// only; "" clears it (review requests stop). */
  updateSettings: (payload: Partial<WaSettings>) => apiClient.put<ApiSuccess<WaSettings>>("/whatsapp/crm/settings", payload).then((r) => r.data.data),
  uploadMedia: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return apiClient.post<ApiSuccess<{ media_id: string; media_type: string; filename: string; size: number }>>("/whatsapp/crm/media", form, { headers: { "Content-Type": "multipart/form-data" }, timeout: UPLOAD_TIMEOUT_MS }).then((r) => r.data.data);
  },
  mediaUrl: (mediaId: string) => `${API_BASE_URL}/whatsapp/crm/media/${mediaId}`,
  /** The attachment itself, fetched WITH the admin's bearer token (a bare
   *  URL in an <img>/<a> can't carry it — the route is admin-only). */
  mediaBlob: (mediaId: string) =>
    apiClient.get<Blob>(`/whatsapp/crm/media/${encodeURIComponent(mediaId)}`, { responseType: "blob" }).then((r) => r.data),
};

// ---- Service zones (polygon coverage) -------------------------------------
export type ServiceZone = {
  id: string;
  name: string;
  service_center_id: string;
  polygon: { type: "Polygon"; coordinates: number[][][] };
  is_active: boolean;
};

export const zonesApi = {
  list: () => apiClient.get<ApiSuccess<ServiceZone[]>>("/service-zones").then((r) => r.data.data),
  create: (payload: { name: string; service_center_id: string; ring: number[][] }) =>
    apiClient.post<ApiSuccess<ServiceZone>>("/service-zones", payload).then((r) => r.data.data),
  update: (id: string, payload: { name?: string; ring?: number[][]; is_active?: boolean }) =>
    apiClient.put<ApiSuccess<ServiceZone>>(`/service-zones/${id}`, payload).then((r) => r.data.data),
  remove: (id: string) => apiClient.delete(`/service-zones/${id}`),
};
