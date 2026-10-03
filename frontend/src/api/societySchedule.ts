/** Society premium-wash scheduling — see docs/SOCIETY_PLANS.md §9. */
import { apiClient, type ApiSuccess } from "../lib/api-client";
import type { Person } from "./society";

export type PatternKind = "weekly" | "monthly_nth" | "every_n_weeks";

export interface RulePattern {
  kind: PatternKind;
  /** Monday = 0 … Sunday = 6 */
  weekday: number;
  weeks?: number[];
  interval_weeks?: number;
  anchor_date?: string | null;
}

export interface ScheduleRule {
  id: string;
  society_id: string;
  kind: "society" | "resident";
  pattern: RulePattern;
  label: string;
  slot_keys: string[];
  window_label: string;
  captains: { id: string; name?: string | null }[];
  washes_per_captain: number;
  enrollment_id?: string | null;
  subscription_ids: string[];
  start_date?: string | null;
  end_date?: string | null;
  notes?: string | null;
  is_active: boolean;
}

export interface VisitCar {
  sub_id: string;
  plate?: string | null;
  vehicle_type_name?: string | null;
  /** The premium wash this car gets ("Star Wash"). */
  service_name?: string | null;
}

export interface VisitAllocation {
  enrollment_id?: string | null;
  resident_name?: string | null;
  flat?: string | null;
  cars: VisitCar[];
  captain_id?: string | null;
  captain_name?: string | null;
  slot_key?: string | null;
  slot_label?: string | null;
  start?: string | null;
  start_label?: string | null;
  status: "projected" | "booked" | "failed" | "cancelled" | "overflow";
  booking_ids: string[];
  booking_statuses: (string | null)[];
  error?: string | null;
  warning?: string | null;
  reason?: string | null;
  lane?: number | null;
}

export interface SkippedCar {
  sub_id: string;
  plate?: string | null;
  vehicle_type_name?: string | null;
  flat?: string | null;
  resident_name?: string | null;
  reason: string;
}

export type VisitStatus = "planned" | "skipped" | "generating" | "booked" | "partial" | "failed" | "empty" | "cancelled";

export interface SocietyVisit {
  id: string;
  society_id: string;
  society_name?: string | null;
  kind: "society" | "resident";
  date: string;
  date_label: string;
  rule_id?: string | null;
  rule_date?: string | null;
  moved_from?: string | null;
  slot_keys: string[];
  window_label: string;
  captains: { id: string; name?: string | null }[];
  fallback_captain?: { id: string; name?: string | null } | null;
  washes_per_captain?: number | null;
  capacity?: number | null;
  enrollment_id?: string | null;
  subscription_ids: string[];
  excluded_subscription_ids: string[];
  status: VisitStatus;
  display_status: VisitStatus | "missed" | "done";
  generate_on?: string | null;
  generated_at?: string | null;
  generation_error?: string | null;
  allocations: VisitAllocation[];
  allocated_cars: number;
  overflow: VisitAllocation[];
  skipped: SkippedCar[];
  changes: { at: string; by: string; what: string }[];
  notes?: string | null;
  editable: boolean;
}

export interface ScheduleWarning {
  kind: "captain_double" | "captain_leave" | "slot_full" | "overflow";
  date: string;
  visit_ids: string[];
  message: string;
}

export interface CarOutlook {
  sub_id: string;
  society_id: string;
  plate?: string | null;
  flat?: string | null;
  resident_name?: string | null;
  enrollment_id?: string | null;
  vehicle_type_name?: string | null;
  service_name?: string | null;
  remaining: number;
  total: number;
  end_date: string;
  booked_upcoming: number;
  planned: number;
  at_risk: number;
}

export interface ScheduleRequest {
  id: string;
  visit_id: string;
  society_id: string;
  society_name?: string | null;
  resident_name?: string | null;
  flat?: string | null;
  plates: string[];
  /** The same cars with their car type ("MP09SR5272 · Hatchback"). */
  cars?: { plate?: string | null; vehicle_type_name?: string | null }[];
  subscription_ids: string[];
  visit_date?: string | null;
  visit_date_label?: string | null;
  slot_label?: string | null;
  kind: "skip" | "move";
  preferred_date?: string | null;
  preferred_date_label?: string | null;
  preferred_slot?: string | null;
  preferred_slot_label?: string | null;
  note?: string | null;
  status: "pending" | "approved" | "declined";
  resolution_note?: string | null;
  created_at?: string | null;
}

export interface ScheduleSettings {
  generate_days_ahead: number;
  default_washes_per_captain: number;
}

export interface SlotOption {
  key: string;
  label: string;
}

export interface SocietySchedule {
  society: { id: string; name: string; service_center_id: string; daily_captain_id?: string | null };
  start: string;
  end: string;
  today: string;
  settings: ScheduleSettings;
  slots: SlotOption[];
  captains: Person[];
  rules: ScheduleRule[];
  resident_rules: ScheduleRule[];
  visits: SocietyVisit[];
  outlook: CarOutlook[];
  requests: ScheduleRequest[];
  residents: { enrollment_id: string; resident_name?: string | null; flat?: string | null; cars: CarOutlook[] }[];
  warnings: ScheduleWarning[];
}

export interface PlannerSociety {
  id: string;
  name: string;
  area?: string | null;
  active_cars: number;
  premium_left: number;
  at_risk: number;
  rules: ScheduleRule[];
  next_visit: { id: string; date: string; date_label: string } | null;
}

export interface Planner {
  center: { id: string; name: string };
  start: string;
  end: string;
  today: string;
  settings: ScheduleSettings;
  slots: SlotOption[];
  captains: Person[];
  societies: PlannerSociety[];
  visits: SocietyVisit[];
  warnings: ScheduleWarning[];
}

export interface RotationPreview {
  society_id: string;
  society_name: string;
  pattern: RulePattern;
  label: string;
  first_dates: string[];
  first_labels: string[];
}

export interface SocietyRuleInput {
  pattern: RulePattern;
  slot_keys: string[];
  captain_ids: string[];
  washes_per_captain: number;
  start_date?: string | null;
  end_date?: string | null;
  notes?: string | null;
  is_active?: boolean;
}

export interface ResidentRuleInput {
  enrollment_id: string;
  subscription_ids: string[];
  pattern: RulePattern;
  slot_key: string;
  captain_id?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  notes?: string | null;
  is_active?: boolean;
}

export interface VisitInput {
  date: string;
  slot_keys: string[];
  captain_ids: string[];
  washes_per_captain: number;
  notes?: string | null;
}

export interface VisitChange {
  date?: string;
  slot_keys?: string[];
  captain_ids?: string[];
  washes_per_captain?: number;
  notes?: string | null;
}

export interface CaptainVisitCar {
  plate?: string | null;
  vehicle_type_name?: string | null;
  service_name?: string | null;
  flat?: string | null;
  start_label?: string | null;
  slot_label?: string | null;
  booking_id?: string | null;
  status: string;
  mine: boolean;
  captain_name?: string | null;
}

export interface CaptainVisit {
  id: string;
  society_id: string;
  society_name?: string | null;
  address?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  date: string;
  date_label: string;
  kind: "society" | "resident";
  window_label: string;
  status: VisitStatus;
  generate_on?: string | null;
  my_washes: number;
  total_washes: number;
  captains: { id: string; name?: string | null }[];
  cars: CaptainVisitCar[];
}

export interface MyScheduleItem {
  /** null for a premium wash booked by hand (not from a visit day). */
  visit_id: string | null;
  booking_id?: string;
  society_id: string;
  society_name?: string | null;
  date: string;
  date_label: string;
  kind: "society" | "resident" | "booking";
  slot_key?: string | null;
  slot_label?: string | null;
  start_label?: string | null;
  status: "booked" | "planned";
  cars: { sub_id: string; plate?: string | null; vehicle_type_name?: string | null }[];
  can_request: boolean;
}

export interface NextWash {
  date: string;
  date_label: string;
  slot_key?: string | null;
  slot_label?: string | null;
  status: "booked" | "planned";
}

export interface MySchedule {
  items: MyScheduleItem[];
  next_by_subscription: Record<string, NextWash>;
  requests: ScheduleRequest[];
  slots_by_society: Record<string, SlotOption[]>;
  lead_days: number;
}

type Warnings = { id: string; warnings: string[] };
const data = <T,>(p: Promise<{ data: ApiSuccess<T> }>) => p.then((r) => r.data.data);

export const scheduleApi = {
  settings: () => data<ScheduleSettings>(apiClient.get("/society-schedule/settings")),
  saveSettings: (payload: ScheduleSettings) => data<ScheduleSettings>(apiClient.put("/society-schedule/settings", payload)),
  planner: (params: { center_id?: string; start?: string; end?: string }) => data<Planner>(apiClient.get("/society-schedule/planner", { params })),
  society: (societyId: string, params?: { start?: string; end?: string }) =>
    data<SocietySchedule>(apiClient.get(`/society-schedule/societies/${societyId}`, { params })),
  createRule: (societyId: string, payload: SocietyRuleInput) => data<ScheduleRule>(apiClient.post(`/society-schedule/societies/${societyId}/rules`, payload)),
  createResidentRule: (societyId: string, payload: ResidentRuleInput) =>
    data<ScheduleRule>(apiClient.post(`/society-schedule/societies/${societyId}/resident-rules`, payload)),
  updateRule: (ruleId: string, payload: SocietyRuleInput | ResidentRuleInput) => data<ScheduleRule>(apiClient.put(`/society-schedule/rules/${ruleId}`, payload)),
  deleteRule: (ruleId: string) => data<{ id: string }>(apiClient.delete(`/society-schedule/rules/${ruleId}`)),
  rotation: (payload: {
    center_id?: string; society_ids: string[]; weekdays: number[]; start_date: string; slot_keys: string[];
    captain_ids: string[]; washes_per_captain: number; dry_run?: boolean;
  }) => data<{ preview: RotationPreview[]; created: number }>(apiClient.post("/society-schedule/rotation", payload)),
  createVisit: (societyId: string, payload: VisitInput) => data<{ id: string; date: string }>(apiClient.post(`/society-schedule/societies/${societyId}/visits`, payload)),
  updateVisit: (visitId: string, payload: VisitChange) => data<Warnings>(apiClient.patch(`/society-schedule/visits/${visitId}`, payload)),
  skip: (visitId: string) => data<Warnings>(apiClient.post(`/society-schedule/visits/${visitId}/skip`)),
  restore: (visitId: string) => data<Warnings>(apiClient.post(`/society-schedule/visits/${visitId}/restore`)),
  remove: (visitId: string) => data<{ id: string }>(apiClient.delete(`/society-schedule/visits/${visitId}`)),
  exclude: (visitId: string, subscriptionIds: string[], excluded = true) =>
    data<Warnings>(apiClient.post(`/society-schedule/visits/${visitId}/exclude`, { subscription_ids: subscriptionIds, excluded })),
  bookNow: (visitId: string) => data<{ id: string; status: string; booked: number; error?: string | null }>(apiClient.post(`/society-schedule/visits/${visitId}/book`)),
  requests: (params?: { center_id?: string; status?: string }) => data<ScheduleRequest[]>(apiClient.get("/society-schedule/requests", { params })),
  approve: (requestId: string, payload: { note?: string; date?: string; slot_key?: string; captain_id?: string }) =>
    data<ScheduleRequest>(apiClient.post(`/society-schedule/requests/${requestId}/approve`, payload)),
  decline: (requestId: string, note?: string) => data<ScheduleRequest>(apiClient.post(`/society-schedule/requests/${requestId}/decline`, { note })),
  captainVisits: () => data<{ date: string; visits: CaptainVisit[] }>(apiClient.get("/society-schedule/captain/visits")),
  my: (societyId?: string) => data<MySchedule>(apiClient.get("/society-schedule/my", { params: societyId ? { society_id: societyId } : undefined })),
  requestChange: (payload: { visit_id: string; kind: "skip" | "move"; preferred_date?: string; preferred_slot?: string; note?: string }) =>
    data<ScheduleRequest>(apiClient.post("/society-schedule/my/requests", payload)),
};

export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
export const WEEKDAYS_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
