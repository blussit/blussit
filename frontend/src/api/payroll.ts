import { apiClient, type ApiPaginated, type ApiSuccess } from "../lib/api-client";

/**
 * Captain pay: salary / commission terms, the payroll ledger, attendance,
 * deposits and kits (docs/ARCHITECTURE.md §7.13). Every amount is integer
 * PAISE; days are IST `YYYY-MM-DD`, months `YYYY-MM`. Display and typing go
 * through lib/paise.ts — nothing here converts.
 */

export type PayBasis = "salary" | "commission" | "legacy";
export type CommissionMode = "percent" | "per_booking";

export interface KitItem {
  name: string;
  qty: number;
  serial?: string | null;
  /** Whole-paise value of the item; 0 when not known (the server takes no null). */
  value_paise?: number;
}

export interface PayKit {
  provided: boolean;
  items: KitItem[];
  issued_on?: string | null;
  returned_on?: string | null;
  notes?: string | null;
}

export interface PayTerms {
  basis: PayBasis;
  salary_monthly_paise: number;
  /** 0 = Monday … 6 = Sunday. */
  weekly_off: number;
  paid_leaves_per_month: number;
  petrol_per_day_paise: number;
  extra_day_on_off: boolean;
  commission: { mode: CommissionMode; percent: number };
  deposit_expected_paise: number;
  kit: PayKit;
  start_date?: string | null;
  end_date?: string | null;
}

export interface PayHistorySegment {
  effective_from: string;
  basis?: PayBasis | null;
  /** The dated terms in force from `effective_from` (basis, salary, commission, …). */
  terms?: Partial<PayTerms> | null;
  /** Only the fields this change touched, before and after. */
  before?: Record<string, unknown> | null;
  after?: Record<string, unknown> | null;
  reason?: string | null;
  actor_name?: string | null;
  at?: string | null;
}

/** What the center suggests for a new captain (service_centers.operation.default_captain_pay). */
export interface DefaultCaptainPay {
  basis?: PayBasis | "none" | null;
  salary_monthly_paise?: number | null;
  commission_percent?: number | null;
}

/** One row of the Salary page. */
export interface PayrollCaptainRow {
  id: string;
  full_name: string;
  phone?: string | null;
  employee_id?: string | null;
  status: string;
  service_center_id?: string | null;
  service_center_name?: string | null;
  pay: PayTerms;
  earned_paise: number;
  paid_paise: number;
  /** Earned − paid, all time. Negative = an advance / recovery due. */
  dues_paise: number;
  deposit_held_paise: number;
  kit_provided: boolean;
  kit_returned: boolean;
  pending_commissions: number;
  /** false = paid by the franchise partner, never counted in Blussit Finance. */
  finance_impact: boolean;
  basis?: PayBasis;
  legacy?: boolean;
  earned_total_paise?: number;
  paid_total_paise?: number;
}

export interface PayrollSummary {
  month: string;
  active_captains: number;
  legacy_captains: number;
  earned_paise: number;
  paid_paise: number;
  dues_paise: number;
  deposit_held_paise?: number;
  pending_commissions?: number;
  kits_out?: number;
}

/** When one center is in view: how many kits are with captains vs on record. */
export interface CenterKitCounters {
  kits_assigned: number;
  kits_on_record: number;
  kit_warning?: string | null;
}

export interface PayrollCaptainsResult {
  month: string;
  summary: PayrollSummary;
  rows: PayrollCaptainRow[];
  /** Present when a single center is in view: whether its captains are paid by the franchise. */
  finance_impact?: boolean;
  center?: CenterKitCounters;
}

export type LedgerKind =
  | "commission"
  | "commission_adjust"
  | "salary_accrual"
  | "allowance"
  | "bonus"
  | "deduction"
  | "advance"
  | "payout"
  | "payout_void"
  | "deposit_received"
  | "deposit_refund"
  | "deposit_forfeit"
  | "cash_offset";

export interface LedgerEntry {
  id: string;
  kind: LedgerKind;
  /** Signed paise: earnings positive, payouts negative-to-dues. */
  amount_paise: number;
  captain_id?: string;
  /** For a payout: what it was for (salary, commission, petrol, advance, other). */
  payment_kind?: string | null;
  adjustment_kind?: string | null;
  cash_offset_paise?: number;
  booking_id?: string | null;
  booking_number?: string | null;
  period?: string | null;
  method?: string | null;
  note?: string | null;
  finance_impact: boolean;
  voided?: boolean;
  voids?: string | null;
  actor_name?: string | null;
  actor_role?: string | null;
  /** IST day of the payment / entry. */
  entry_date?: string | null;
  created_at: string;
}

export type AttendanceStatus = "present" | "half_day" | "leave" | "absent" | "weekly_off" | "holiday" | "not_joined" | "left" | "upcoming" | "pending";
/** The days a manager can set by hand (the rest are worked out). */
export type AttendanceOverrideStatus = "present" | "half_day" | "leave" | "absent";

export interface AttendanceDay {
  date: string;
  /** The derived-or-overridden status for the day. */
  status: AttendanceStatus;
  overridden?: boolean;
  paid_leave?: boolean;
  paid_fraction?: number;
  /** Where a derived day came from: check_in, job, leave_request, manual. */
  source?: string | null;
  note?: string | null;
  marked_by?: string | null;
}

export interface SalaryMonth {
  expected_working_days: number;
  paid_days: number;
  present_days: number;
  half_days: number;
  leave_days: number;
  absent_days: number;
  holidays: number;
  day_rate_paise: number;
  /** Salary computed from attendance. */
  computed_paise: number;
  petrol_paise: number;
  /** What the manager fixed at finalize, when different from computed. */
  payable_paise: number;
  finalized: boolean;
  finalized_paise?: number | null;
  finalized_petrol_paise?: number | null;
}

export interface PayrollStatement {
  month: string;
  earned_paise: number;
  paid_paise: number;
  dues_paise: number;
  commission_paise: number;
  salary_paise: number;
  allowance_paise: number;
  adjustments_paise: number;
  basis?: PayBasis;
  salary?: SalaryMonth | null;
  attendance: AttendanceDay[];
  entries: LedgerEntry[];
}

export interface PayrollCaptainDetail extends PayrollCaptainRow {
  pay_history: PayHistorySegment[];
  /** Cash the captain currently owes the business (the wallet), for the cash-settle offset. */
  cash_held_paise: number;
  /** Highest commission share allowed on a booking, percent of base. */
  max_commission_percent: number;
  alert_payment_paise: number;
}

export interface CommissionRow {
  id: string;
  booking_id: string;
  booking_number: string;
  completed_at?: string | null;
  customer_name?: string | null;
  vehicle_type?: string | null;
  service_name?: string | null;
  base_paise: number;
  rate_percent?: number | null;
  amount_paise: number;
  edited: boolean;
  mode?: CommissionMode | null;
  auto_paise?: number;
  adjustments_paise?: number;
  completed_day?: string | null;
  /** Per-booking mode with no amount yet. */
  not_set: boolean;
  max_paise: number;
  max_percent: number;
  reason?: string | null;
}

export interface PayrollMe {
  basis: PayBasis;
  month: string;
  pay: Pick<PayTerms, "basis" | "salary_monthly_paise" | "commission" | "petrol_per_day_paise" | "weekly_off" | "paid_leaves_per_month" | "deposit_expected_paise" | "kit">;
  earned_paise: number;
  paid_paise: number;
  dues_paise: number;
  deposit_held_paise: number;
  statement?: PayrollStatement | null;
  commissions: CommissionRow[];
  entries: LedgerEntry[];
  attendance: AttendanceDay[];
  salary?: SalaryMonth | null;
}

export type PaymentKind = "salary" | "commission" | "petrol" | "advance" | "other";
export type PaymentMethod = "cash" | "upi" | "bank" | "other";

export interface PaymentInput {
  amount_paise: number;
  kind: PaymentKind;
  method: PaymentMethod;
  /** IST day paid. */
  date: string;
  note?: string;
  cash_offset_paise?: number;
  idempotency_key: string;
}

export interface AdjustmentInput {
  kind: "allowance" | "bonus" | "deduction";
  amount_paise: number;
  note: string;
  idempotency_key: string;
}

export interface CommissionEditInput {
  amount_paise?: number;
  percent?: number;
  reason: string;
}

export interface TermsInput {
  basis?: PayBasis;
  salary_monthly_paise?: number;
  weekly_off?: number;
  paid_leaves_per_month?: number;
  petrol_per_day_paise?: number;
  extra_day_on_off?: boolean;
  commission?: { mode: CommissionMode; percent: number };
  deposit_expected_paise?: number;
  /** IST day; the server allows two months back and one month ahead. */
  effective_from?: string;
  reason?: string;
}

export interface DepositInput {
  /** "expect" sets the deposit the captain owes; the rest move money. */
  action: "received" | "refund" | "forfeit" | "expect";
  amount_paise: number;
  method?: PaymentMethod;
  note?: string;
  idempotency_key: string;
}

export interface KitInput {
  provided: boolean;
  items: KitItem[];
  issued_on?: string | null;
  notes?: string | null;
}

/** The server's own limits, used only to word the form hints. */
export const PAY_LIMITS = { paidLeavesMax: 10, backdateMonths: 2, aheadMonths: 1 } as const;

/** The pay block a new captain is created with (POST /auth/staff). */
export interface NewCaptainPay {
  basis: "salary" | "commission";
  salary_monthly_paise?: number;
  weekly_off?: number;
  paid_leaves_per_month?: number;
  petrol_per_day_paise?: number;
  commission?: { mode: CommissionMode; percent: number };
  deposit_expected_paise?: number;
  deposit_received?: boolean;
  kit?: { provided: boolean; items: KitItem[] };
}

export interface PayrollListFilters {
  center_id?: string;
  status?: string;
  basis?: string;
  month?: string;
}

const clean = (f: Record<string, unknown>) => Object.fromEntries(Object.entries(f).filter(([, v]) => v !== "" && v !== undefined && v !== null));

/** A fresh key per modal open — a double click or a retry never pays twice. */
export const newIdempotencyKey = (): string =>
  typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;

export interface Written<T> {
  data: T;
  /** The server's own words, shown in the success toast. */
  message: string;
}
const withMessage = <T>(r: { data: ApiSuccess<T> }): Written<T> => ({ data: r.data.data, message: r.data.message });

export const payrollApi = {
  captains: (filters: PayrollListFilters) =>
    apiClient.get<ApiSuccess<PayrollCaptainsResult>>("/payroll/captains", { params: clean({ ...filters }) }).then((r) => r.data.data),
  summary: (filters: { center_id?: string; month?: string }) =>
    apiClient.get<ApiSuccess<PayrollSummary>>("/payroll/summary", { params: clean({ ...filters }) }).then((r) => r.data.data),
  captain: (id: string) => apiClient.get<ApiSuccess<PayrollCaptainDetail>>(`/payroll/captains/${id}`).then((r) => r.data.data),
  statement: (id: string, month: string) =>
    apiClient.get<ApiSuccess<PayrollStatement>>(`/payroll/captains/${id}/statement`, { params: { month } }).then((r) => r.data.data),
  ledger: (id: string, params?: { page?: number; page_size?: number }) =>
    apiClient.get<ApiPaginated<LedgerEntry>>(`/payroll/captains/${id}/ledger`, { params }).then((r) => r.data),
  commissions: (id: string, month: string) =>
    apiClient.get<ApiSuccess<CommissionRow[]>>(`/payroll/captains/${id}/commissions`, { params: { month } }).then((r) => r.data.data),

  saveTerms: (id: string, payload: TermsInput) => apiClient.put<ApiSuccess<PayrollCaptainDetail>>(`/payroll/captains/${id}/terms`, payload).then(withMessage),
  pay: (id: string, payload: PaymentInput) => apiClient.post<ApiSuccess<LedgerEntry>>(`/payroll/captains/${id}/payments`, payload).then(withMessage),
  voidPayment: (ledgerId: string, reason: string) =>
    apiClient.post<ApiSuccess<LedgerEntry>>(`/payroll/payments/${ledgerId}/void`, { reason }).then(withMessage),
  adjust: (id: string, payload: AdjustmentInput) => apiClient.post<ApiSuccess<LedgerEntry>>(`/payroll/captains/${id}/adjustments`, payload).then(withMessage),
  markAttendance: (id: string, payload: { date: string; status: AttendanceOverrideStatus; paid_leave?: boolean; note: string }) =>
    apiClient.post<ApiSuccess<AttendanceDay>>(`/payroll/captains/${id}/attendance`, payload).then(withMessage),
  editCommission: (bookingId: string, payload: CommissionEditInput) =>
    apiClient.put<ApiSuccess<CommissionRow>>(`/payroll/bookings/${bookingId}/commission`, payload).then(withMessage),
  finalize: (id: string, month: string, payload: { override_amount_paise?: number; reason?: string }) =>
    apiClient.post<ApiSuccess<SalaryMonth & { posted?: boolean }>>(`/payroll/captains/${id}/months/${month}/finalize`, payload).then(withMessage),
  deposit: (id: string, payload: DepositInput) => apiClient.post<ApiSuccess<PayrollCaptainDetail>>(`/payroll/captains/${id}/deposit`, payload).then(withMessage),
  kit: (id: string, payload: KitInput) => apiClient.put<ApiSuccess<PayrollCaptainDetail>>(`/payroll/captains/${id}/kit`, payload).then(withMessage),
  returnKit: (id: string, payload: { returned_on?: string; note?: string }) =>
    apiClient.post<ApiSuccess<PayrollCaptainDetail>>(`/payroll/captains/${id}/kit/return`, payload).then(withMessage),
  exportCsv: (filters: PayrollListFilters) =>
    apiClient.get<Blob>("/payroll/export.csv", { params: clean({ ...filters }), responseType: "blob" }).then((r) => r.data),

  /** Captain app — their own pay only. */
  me: () => apiClient.get<ApiSuccess<PayrollMe>>("/payroll/me").then((r) => r.data.data),
  meStatement: (month: string) => apiClient.get<ApiSuccess<PayrollStatement>>("/payroll/me/statement", { params: { month } }).then((r) => r.data.data),

  /** Create a captain with pay terms (manager and admin forms). */
  createCaptain: (payload: {
    full_name: string;
    email?: string;
    phone?: string;
    password: string;
    service_center_id?: string;
    photo_url?: string;
    pay: NewCaptainPay;
  }) => apiClient.post<ApiSuccess<{ id: string }>>("/auth/staff", { ...payload, role: "captain" }).then((r) => r.data.data),
};
