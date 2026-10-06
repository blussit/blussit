/** Society plans — see docs/SOCIETY_PLANS.md. */
import { apiClient, type ApiSuccess } from "../lib/api-client";
import type { AuthResult } from "./auth";
import { titleCase } from "../components/public/landing/shared";
import { formatClockIST } from "../lib/date";

export interface PricePair {
  price: number;
  mrp: number;
}

export interface SocietyPlanOption {
  id: string;
  name: string;
  description?: string | null;
  bucket_days: number;
  bucket_label: string;
  premium_service_id: string;
  premium_service_name?: string | null;
  premium_count: number;
  scope: "template" | "society" | "customer";
  auto_created: boolean;
  /** vehicle_type_id -> selling price + MRP for one car. */
  prices: Record<string, PricePair>;
}

export interface SocietyPlanAdmin extends SocietyPlanOption {
  is_active: boolean;
  display_order: number;
  flat_price: number;
  flat_mrp: number;
  vehicle_type_prices: Record<string, number>;
  vehicle_type_mrps: Record<string, number>;
  vehicle_types: string[];
  society_ids: string[];
  society_names: string[];
  customer_name?: string | null;
  customer_phone?: string | null;
  active_cars: number;
}

export interface CarType {
  id: string;
  name: string;
  slug?: string;
}

export interface SocietyFormData {
  society: { name: string; area?: string | null; city?: string | null };
  plans: SocietyPlanOption[];
  customise: {
    enabled: boolean;
    bucket_day_options: { days: number; label: string }[];
    premium_count_options: number[];
    premium_services: { id: string; name: string }[];
  };
  vehicle_types: CarType[];
  online_payment: boolean;
  lead_days: number;
}

export interface CustomCombo {
  bucket_days: number;
  premium_service_id: string;
  premium_count: number;
}

export interface PlanChoice {
  plan_id?: string;
  custom?: CustomCombo;
}

/** A coupon checked against a society total — discount in whole rupees,
 *  or why it can't be used. */
export interface CouponCheck {
  code: string;
  valid: boolean;
  discount: number;
  error: string | null;
}

export interface SocietyQuote {
  plan_id: string | null;
  plan_name: string;
  bucket_label: string;
  premium_service_name?: string | null;
  premium_count: number;
  cars: { vehicle_type: string; vehicle_type_name?: string; price: number; mrp: number }[];
  total: number;
  mrp_total: number;
  coupon: CouponCheck | null;
  discount: number;
  payable_total: number;
}

export interface CouponPreview {
  subtotal: number;
  discount: number;
  payable: number;
  coupon: CouponCheck | null;
}

export interface CarInput {
  vehicle_type: string;
  registration_number: string;
}

export interface EnrollPayload extends PlanChoice {
  resident_name: string;
  phone: string;
  flat: string;
  cars: CarInput[];
  pay_now?: boolean;
  phone_otp?: string;
  phone_access_token?: string;
  coupon_code?: string;
}

export interface EnrollmentCar {
  vehicle_id: string;
  vehicle_type: string;
  vehicle_type_name?: string;
  registration_number: string;
  price: number;
  mrp: number;
  status: "pending" | "active" | "cancelled" | "skipped";
  note?: string | null;
  subscription: null | {
    id: string;
    status: "active" | "expired" | "cancelled";
    remaining: number;
    total: number;
    end_date: string | null;
    cycle_start: string | null;
    can_renew: boolean;
  };
  bucket_used: number | null;
  bucket_allowance: number;
}

export interface SocietyEnrollment {
  id: string;
  society_id: string;
  customer_id: string;
  resident_name: string;
  phone: string;
  flat: string;
  plan_id: string;
  plan_name: string;
  bucket_days: number;
  bucket_label: string;
  /** "Daily wash" / "Alternate-day wash" — the bucket wash's name on a counter. */
  bucket_short_label: string;
  premium_service_id: string;
  /** The premium wash by name ("Star Wash", "Deep Cleaning"). */
  premium_service_name: string;
  premium_count: number;
  source: "form" | "manager";
  status: "requested" | "awaiting_payment" | "active" | "cancelled";
  /** Version of the request shown — sent back when marking it paid. */
  revision: number;
  cars: EnrollmentCar[];
  total_amount: number;
  mrp_total: number;
  coupon_code?: string | null;
  discount_amount: number;
  /** What's due on activation: total minus the coupon. */
  payable_amount: number;
  payment: { method: string; amount: number; discount?: number; coupon_code?: string | null; at: string | null } | null;
  renew_open: boolean;
  renew_amount: number;
  /** Staff list only: the unpaid WhatsApp payment link, if one was sent. */
  payment_link?: { short_url: string; amount: number; renewal: boolean; sent_at: string | null } | null;
  created_at: string | null;
  activated_at: string | null;
  cancelled_at: string | null;
}

export interface SocietyHub {
  society: { id: string; name: string; area?: string | null; city?: string | null };
  service_center_id: string;
  enrollments: SocietyEnrollment[];
  upcoming: {
    id: string;
    booking_number: string;
    scheduled_date: string;
    scheduled_slot: string;
    slot_label: string;
    status: string;
    registration_number?: string | null;
    vehicle_type_name?: string | null;
    service_name?: string | null;
    plan_label?: string | null;
  }[];
  online_payment: boolean;
  lead_days: number;
}

export interface Person {
  id: string;
  name?: string | null;
  phone?: string | null;
}

export interface SocietyDetail {
  id: string;
  name: string;
  address_line: string;
  area?: string | null;
  city?: string | null;
  state?: string | null;
  pincode: string;
  latitude?: number | null;
  longitude?: number | null;
  contact_name?: string | null;
  contact_phone?: string | null;
  notes?: string | null;
  service_center_id: string;
  service_center_name?: string | null;
  form_token: string;
  form_path: string;
  form_enabled: boolean;
  is_active: boolean;
  daily_captain: Person | null;
  substitute: { date: string; captain: Person | null } | null;
  today_captain: Person | null;
  today_attendance: null | {
    arrived_at: string | null;
    captain: Person | null;
    washed_count: number;
    far_from_society: boolean;
    location_missing: boolean;
  };
  residents: number;
  requests: number;
  active_cars: number;
  open_issues: number;
  created_at: string | null;
  payments?: {
    id: string; amount: number; method: string; kind: string; car_count: number; resident_name?: string; created_at: string;
    flat?: string | null;
    /** What the plan covers — "Daily wash + 2 Star Wash". */
    plan_services?: string | null;
    cars?: { registration_number?: string | null; vehicle_type_name?: string | null }[];
  }[];
}

export interface SocietyRow {
  id: string;
  name: string;
  area?: string | null;
  city?: string | null;
  pincode: string;
  service_center_id: string;
  service_center_name?: string | null;
  today_captain_name?: string | null;
  attended_today: boolean;
  revenue_month: number;
  attendance_days_month: number;
  form_enabled: boolean;
  is_active: boolean;
  residents: number;
  requests: number;
  active_cars: number;
  open_issues: number;
}

export interface SocietyList {
  kpis: { societies: number; residents: number; active_cars: number; requests: number; open_issues: number; revenue_month: number; attended_today: number };
  rows: SocietyRow[];
  month: string;
}

export interface AttendanceDay {
  date: string;
  present: boolean;
  missed: boolean;
  future: boolean;
  arrived_at: string | null;
  captain_name: string | null;
  washed_count: number;
  far_from_society: boolean;
  location_missing: boolean;
  distance_m: number | null;
}

export interface AttendanceMonth {
  month: string;
  days: AttendanceDay[];
  present_days: number;
  missed_days: number;
  washes: number;
}

export interface CaptainSocietyCar {
  vehicle_id: string;
  registration_number: string | null;
  vehicle_type_name?: string | null;
  flat?: string | null;
  used: number;
  allowance: number;
  allowance_left: number;
  bucket_label: string;
  premium_service_name: string;
  premium_remaining: number;
  premium_total: number;
  washed_today: boolean;
}

export interface CaptainSocietyCard {
  id: string;
  name: string;
  address_line?: string | null;
  area?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  is_substitute: boolean;
  attendance: null | {
    arrived_at: string | null;
    distance_m: number | null;
    far_from_society: boolean;
    location_missing: boolean;
    washed_count: number;
  };
  cars: CaptainSocietyCar[];
}

export interface RateCard {
  bucket_day_price: { default: number; by_type: Record<string, number> };
  bucket_day_mrp: { default: number; by_type: Record<string, number> };
  premium_discount_percent: number;
  bucket_day_options: number[];
  premium_count_options: number[];
  premium_service_ids: string[];
  allow_customise: boolean;
  premium_services: { id: string; name: string }[];
  vehicle_types: CarType[];
}

export interface SocietyInput {
  name: string;
  address_line: string;
  area?: string;
  city?: string;
  state?: string;
  pincode: string;
  latitude: number;
  longitude: number;
  contact_name?: string;
  contact_phone?: string;
  notes?: string;
  service_center_id?: string;
  /** Registering from a landing-page society request links it. */
  lead_id?: string;
}

export interface SocietyPlanInput {
  name: string;
  description?: string;
  bucket_days: number;
  premium_service_id: string;
  premium_count: number;
  price: number;
  mrp?: number;
  vehicle_type_prices?: Record<string, number>;
  vehicle_type_mrps?: Record<string, number>;
  vehicle_types?: string[];
  scope: "template" | "society" | "customer";
  society_ids?: string[];
  customer_phone?: string;
  is_active?: boolean;
  display_order?: number;
}

export interface PremiumBookingResult {
  bookings: { id: string; booking_number: string; status: string; total_amount: number }[];
  scheduled_date: string;
  scheduled_slot: string;
  slot_label: string;
}

const data = <T,>(p: Promise<{ data: ApiSuccess<T> }>) => p.then((r) => r.data.data);

export const societyFormApi = {
  get: (token: string) => data<SocietyFormData>(apiClient.get(`/society-forms/${token}`)),
  quote: (token: string, payload: PlanChoice & { vehicle_types: string[]; coupon_code?: string }) =>
    data<SocietyQuote>(apiClient.post(`/society-forms/${token}/quote`, payload)),
  enroll: (token: string, payload: EnrollPayload) =>
    data<{ enrollment: SocietyEnrollment; auth: AuthResult | null }>(apiClient.post(`/society-forms/${token}/enroll`, payload)),
  me: (token: string) => data<SocietyHub>(apiClient.get(`/society-forms/${token}/me`)),
  withdraw: (token: string, enrollmentId: string) =>
    data<SocietyEnrollment>(apiClient.post(`/society-forms/${token}/me/enrollments/${enrollmentId}/withdraw`)),
  couponPreview: (token: string, enrollmentId: string, payload: { coupon_code?: string; renewal?: boolean }) =>
    data<CouponPreview>(apiClient.post(`/society-forms/${token}/me/enrollments/${enrollmentId}/coupon-preview`, payload)),
  bookPremium: (payload: { subscription_ids: string[]; scheduled_date: string; scheduled_slot: string; notes?: string }) =>
    data<PremiumBookingResult>(apiClient.post("/societies/my/premium-bookings", payload)),
};

export const societyApi = {
  list: (params?: { center_id?: string; search?: string; month?: string }) => data<SocietyList>(apiClient.get("/societies", { params })),
  create: (payload: SocietyInput) => data<SocietyDetail>(apiClient.post("/societies", payload)),
  get: (id: string) => data<SocietyDetail>(apiClient.get(`/societies/${id}`)),
  update: (id: string, payload: Partial<SocietyInput> & { form_enabled?: boolean; is_active?: boolean }) =>
    data<SocietyDetail>(apiClient.put(`/societies/${id}`, payload)),
  rotateLink: (id: string) => data<SocietyDetail>(apiClient.post(`/societies/${id}/rotate-link`)),
  captains: (centerId?: string) => data<Person[]>(apiClient.get("/societies/captains", { params: centerId ? { center_id: centerId } : undefined })),
  setCaptain: (id: string, payload: { captain_id: string | null; date?: string }) => data<SocietyDetail>(apiClient.put(`/societies/${id}/captain`, payload)),
  enrollments: (id: string, status?: string) => data<SocietyEnrollment[]>(apiClient.get(`/societies/${id}/enrollments`, { params: status ? { status } : undefined })),
  plansOffered: (id: string, phone?: string) => data<SocietyFormData>(apiClient.get(`/societies/${id}/plans`, { params: phone ? { phone } : undefined })),
  quote: (id: string, payload: PlanChoice & { vehicle_types: string[]; coupon_code?: string }) => data<SocietyQuote>(apiClient.post(`/societies/${id}/quote`, payload)),
  addResident: (id: string, payload: EnrollPayload & { collect_cash?: boolean }) =>
    data<SocietyEnrollment>(apiClient.post(`/societies/${id}/enrollments`, payload)),
  attendance: (id: string, month?: string) => data<AttendanceMonth>(apiClient.get(`/societies/${id}/attendance`, { params: month ? { month } : undefined })),
  bookPremium: (id: string, payload: { subscription_ids: string[]; scheduled_date: string; scheduled_slot: string; notes?: string }) =>
    data<PremiumBookingResult>(apiClient.post(`/societies/${id}/premium-bookings`, payload)),
  activate: (enrollmentId: string, revision: number, coupon?: { coupon_code?: string; remove_coupon?: boolean }) =>
    data<SocietyEnrollment>(
      apiClient.post(`/society-enrollments/${enrollmentId}/activate`, { method: "cash", expected_revision: revision, ...(coupon || {}) }),
    ),
  renew: (enrollmentId: string, couponCode?: string) =>
    data<SocietyEnrollment>(apiClient.post(`/society-enrollments/${enrollmentId}/renew`, { method: "cash", coupon_code: couponCode || undefined })),
  couponPreview: (enrollmentId: string, payload: { coupon_code?: string; renewal?: boolean }) =>
    data<CouponPreview>(apiClient.post(`/society-enrollments/${enrollmentId}/coupon-preview`, payload)),
  cancel: (enrollmentId: string, vehicleIds?: string[]) =>
    data<SocietyEnrollment>(apiClient.post(`/society-enrollments/${enrollmentId}/cancel`, { vehicle_ids: vehicleIds ?? null })),
  /** Razorpay link to the resident's WhatsApp — paying it activates (or renews) the plan by itself. */
  paymentLink: (enrollmentId: string, renewal = false) =>
    data<{ short_url: string; amount: number; order_id: string; reused: boolean; sent: boolean }>(
      apiClient.post(`/society-enrollments/${enrollmentId}/payment-link`, { renewal, send_whatsapp: true }),
    ),
};

export const societyPlanApi = {
  list: () => data<SocietyPlanAdmin[]>(apiClient.get("/society-plans")),
  create: (payload: SocietyPlanInput) => data<SocietyPlanAdmin>(apiClient.post("/society-plans", payload)),
  update: (id: string, payload: Partial<SocietyPlanInput>) => data<SocietyPlanAdmin>(apiClient.put(`/society-plans/${id}`, payload)),
  rateCard: () => data<RateCard>(apiClient.get("/society-plans/rate-card")),
  saveRateCard: (payload: Omit<RateCard, "premium_services" | "vehicle_types">) => data<RateCard>(apiClient.put("/society-plans/rate-card", payload)),
};

export const captainSocietyApi = {
  today: () => data<{ date: string; societies: CaptainSocietyCard[] }>(apiClient.get("/societies/captain/today")),
  arrive: (id: string, loc: { latitude?: number; longitude?: number; accuracy_m?: number }) =>
    data<CaptainSocietyCard>(apiClient.post(`/societies/captain/${id}/arrive`, loc)),
  washed: (id: string, vehicleIds: string[]) => data<CaptainSocietyCard>(apiClient.put(`/societies/captain/${id}/washed`, { vehicle_ids: vehicleIds })),
};

/** The resident form link for a society, on this site's own origin. */
export function societyFormUrl(formPath: string): string {
  return `${window.location.origin}${formPath}`;
}

export function whatsappShareUrl(text: string): string {
  return `https://wa.me/?text=${encodeURIComponent(text)}`;
}

export const rupees = (n: number | null | undefined) => `₹${Math.round(Number(n || 0)).toLocaleString("en-IN")}`;

/** "Star Wash 1/2" — premium washes left of this cycle's quota. */
export const premiumUsage = (name: string | null | undefined, remaining: number, total: number) => `${titleCase(name) || "Premium Wash"} ${remaining}/${total}`;

/** "Daily Wash 3/25" — bucket washes done of this cycle's allowance. */
export const bucketUsage = (label: string | null | undefined, used: number, allowance: number) => `${titleCase(label) || "Daily Wash"} ${used}/${allowance}`;

/** "Daily Wash + 2 Star Wash" — the services a society plan covers. */
export const planServices = (p: { bucket_label?: string | null; premium_count?: number | null; premium_service_name?: string | null }) =>
  titleCase([p.bucket_label, p.premium_count ? `${p.premium_count} ${p.premium_service_name || "Premium Wash"}` : ""].filter(Boolean).join(" + "));

type PlanLike = { plan_name?: string | null; name?: string | null; bucket_label?: string | null; premium_count?: number | null; premium_service_name?: string | null };
const squash = (s: string) => s.toLowerCase().replace(/\W/g, "");

/** What the plan covers — empty when its name already says exactly that. */
export const planCovers = (p: PlanLike) => {
  const covers = planServices(p);
  const name = p.plan_name ?? p.name;
  return name && squash(name) === squash(covers) ? "" : covers;
};

/** "Gold Plan · Daily Wash + 2 Star Wash" — the plan's name, plus what it
 *  covers when the name doesn't already say it. */
export const planWithServices = (p: PlanLike) => {
  const name = titleCase(p.plan_name ?? p.name);
  const covers = planCovers(p);
  return [name, covers].filter(Boolean).join(" · ");
};

/** "MP09SR5272 · Hatchback" — a car's plate with its car type. */
export const carLabel = (plate: string | null | undefined, type: string | null | undefined) => [plate || "—", titleCase(type)].filter(Boolean).join(" · ");

/** "2:38 AM" — a clock time with an upper-case AM/PM. */
export const clockIST = (iso: string | null | undefined) => formatClockIST(iso).toUpperCase();

export { societyPlanSuffix } from "../lib/bookingGroups";

// ---------------------------------------------------------------------------
// Resident issues (support tickets tagged with the society)
// ---------------------------------------------------------------------------

export const SOCIETY_ISSUE_TYPES = [
  { key: "daily_wash_missed", label: "Daily Wash Missed" },
  { key: "not_cleaned_properly", label: "Car Not Cleaned Properly" },
  { key: "captain_no_show", label: "Captain Didn't Arrive" },
  { key: "premium_not_scheduled", label: "Premium Wash Not Scheduled" },
  { key: "billing", label: "Billing / Payment" },
  { key: "other", label: "Other" },
] as const;
export type SocietyIssueType = (typeof SOCIETY_ISSUE_TYPES)[number]["key"];

export interface SocietyIssue {
  id: string;
  customer_id: string;
  customer_name?: string | null;
  service_center_id?: string | null;
  subject: string;
  description: string;
  priority: "low" | "medium" | "high" | "urgent";
  status: "open" | "in_progress" | "resolved" | "closed";
  category: "society";
  society_id: string;
  society_name?: string | null;
  issue_type: SocietyIssueType;
  issue_label: string;
  registration_number?: string | null;
  vehicle_type_name?: string | null;
  flat?: string | null;
  replies?: { author_role: string; author_name?: string | null; message: string; created_at: string }[];
  created_at: string;
}

export const societyIssueApi = {
  raise: (payload: { society_id: string; issue_type: SocietyIssueType; note?: string; vehicle_id?: string }) =>
    data<SocietyIssue>(apiClient.post("/society-issues", payload)),
  mine: (societyId?: string) => data<SocietyIssue[]>(apiClient.get("/society-issues/my", { params: societyId ? { society_id: societyId } : undefined })),
  forSociety: (societyId: string, status?: string) =>
    data<SocietyIssue[]>(apiClient.get(`/society-issues/society/${societyId}`, { params: status ? { status } : undefined })),
};

// ---------------------------------------------------------------------------
// Society requests (landing page leads)
// ---------------------------------------------------------------------------

export type SocietyLeadStatus = "new" | "contacted" | "registered" | "closed";

export interface SocietyLead {
  id: string;
  society_name: string;
  area: string;
  pincode: string;
  contact_name: string;
  phone: string;
  approx_cars: number;
  note?: string | null;
  staff_note?: string | null;
  status: SocietyLeadStatus;
  requests_count: number;
  service_center_id?: string | null;
  service_center_name?: string | null;
  society_id?: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface SocietyLeadInput {
  society_name: string;
  area: string;
  pincode: string;
  contact_name: string;
  phone: string;
  approx_cars: number;
  note?: string;
}

export const societyLeadApi = {
  create: (payload: SocietyLeadInput) => apiClient.post<ApiSuccess<null>>("/society-leads", payload).then((r) => r.data.message),
  info: () => data<{ from_price: number | null }>(apiClient.get("/society-leads/info")),
  list: (params?: { status?: string; center_id?: string }) =>
    data<{ rows: SocietyLead[]; counts: Record<SocietyLeadStatus, number> }>(apiClient.get("/society-leads", { params })),
  update: (id: string, payload: { status?: SocietyLeadStatus; staff_note?: string }) => data<SocietyLead>(apiClient.put(`/society-leads/${id}`, payload)),
};
