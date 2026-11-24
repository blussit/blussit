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
  /** Weekly-off weekdays, 0 = Monday … 6 = Sunday (default Saturday + Sunday). */
  weekly_off_days: number[];
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
  invoice_id?: string | null;
  invoice_number?: string | null;
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
  /** A plan / pass covered the wash: its commission is set by hand, never auto-computed. */
  plan_covered?: boolean;
  plan_base_paise?: number;
  plan_wash_paise?: number | null;
  max_paise: number;
  max_percent: number;
  reason?: string | null;
  /** Paid / unpaid state against invoices (§7.14). */
  payment_state?: "paid" | "part_paid" | "unpaid" | "not_set" | "none";
  settled_paise?: number;
  unsettled_paise?: number;
  invoice_numbers?: string[];
  invoice_ids?: string[];
  /** Part of what is settled that was marked paid by hand (no invoice). */
  marked_paise?: number;
}

export interface CashBooking {
  booking_id: string;
  booking_number: string;
  completed_at?: string | null;
  day?: string | null;
  collected_paise: number;
  returned_paise: number;
  held_paise: number;
}
export interface CashReceipt {
  id: string;
  captain_id: string;
  amount_paise: number;
  kind: "received" | "pay_offset";
  status: "active" | "void";
  note?: string | null;
  allocations: { booking_id: string; booking_number?: string | null; amount_paise: number }[];
  actor_name?: string | null;
  invoice_number?: string | null;
  created_at: string;
  void?: { at: string; by: string; reason: string } | null;
  duplicate?: boolean;
}
export interface CashPosition {
  captain_id: string;
  captain_name?: string | null;
  held_paise: number;
  bookings: CashBooking[];
  receipts: CashReceipt[];
}
export interface BookingCaptainMoney {
  booking_id: string;
  booking_number: string;
  status: string;
  captain_id?: string | null;
  captain_name?: string | null;
  basis?: PayBasis | null;
  finance_impact: boolean;
  commission: CommissionRow | null;
  cash: (Pick<CashBooking, "collected_paise" | "returned_paise" | "held_paise"> & { captain_id: string; receipts: CashReceipt[] }) | null;
  adjustments: LedgerEntry[];
}

export interface PayrollMe {
  basis: PayBasis;
  month: string;
  pay: Pick<PayTerms, "basis" | "salary_monthly_paise" | "commission" | "petrol_per_day_paise" | "weekly_off_days" | "paid_leaves_per_month" | "deposit_expected_paise" | "kit">;
  earned_paise: number;
  paid_paise: number;
  dues_paise: number;
  deposit_held_paise: number;
  statement?: PayrollStatement | null;
  commissions: CommissionRow[];
  entries: LedgerEntry[];
  attendance: AttendanceDay[];
  salary?: SalaryMonth | null;
  /** The captain's latest paid invoices, newest first. */
  invoices?: PayrollInvoice[];
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
  /** Ties the money to one booking (it shows on that booking's captain money). */
  booking_id?: string;
}

export interface CommissionEditInput {
  amount_paise?: number;
  percent?: number;
  reason: string;
}

export interface TermsInput {
  basis?: PayBasis;
  salary_monthly_paise?: number;
  weekly_off_days?: number[];
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
  weekly_off_days?: number[];
  paid_leaves_per_month?: number;
  petrol_per_day_paise?: number;
  commission?: { mode: CommissionMode; percent: number };
  deposit_expected_paise?: number;
  deposit_received?: boolean;
  kit?: { provided: boolean; items: KitItem[] };
}

export type InvoiceKind = "salary" | "commission" | "petrol" | "advance" | "other";
export type InvoiceStatus = "paid" | "void";

export interface InvoiceLine {
  type: string;
  label?: string;
  amount_paise: number;
  /** salary */
  month?: string;
  expected_working_days?: number;
  present_days?: number;
  half_days?: number;
  leave_days?: number;
  paid_leave_days?: number;
  unpaid_days?: number;
  holidays?: number;
  paid_days?: number;
  computed_paise?: number;
  /** commission */
  booking_id?: string;
  booking_number?: string;
  date?: string | null;
  vehicle_type?: string | null;
  service_name?: string | null;
  base_paise?: number;
  kind?: "new" | "revised";
  settle?: boolean;
}

/** A payment's invoice / payslip (§7.14). Gross amounts, no tax. */
export interface PayrollInvoice {
  id: string;
  number: string;
  captain_id: string;
  captain_name?: string | null;
  captain_employee_id?: string | null;
  service_center_id?: string | null;
  service_center_name?: string | null;
  kind: InvoiceKind;
  month?: string | null;
  period_from?: string | null;
  period_to?: string | null;
  gross_paise: number;
  previously_paid_paise: number;
  due_paise: number;
  paid_paise: number;
  adjustment_paise: number;
  adjustment_reason?: string | null;
  cash_offset_paise: number;
  net_paise: number;
  method: PaymentMethod;
  paid_on: string;
  paid_at?: string | null;
  note?: string | null;
  status: InvoiceStatus;
  ledger_id?: string;
  line_count: number;
  actor_name?: string | null;
  finance_impact?: boolean;
  void?: { at?: string | null; by?: string | null; reason?: string | null } | null;
  created_at: string;
  lines?: InvoiceLine[];
  /** Only on a pay response. */
  duplicate?: boolean;
  dues_paise?: number;
}

export interface InvoiceListFilters {
  center_id?: string;
  captain_id?: string;
  kind?: string;
  status?: string;
  from?: string;
  to?: string;
  page?: number;
  page_size?: number;
}

export interface InvoicePage {
  data: PayrollInvoice[];
  meta: { page: number; page_size: number; total: number; total_pages: number };
  totals: { paid_paise: number; count: number };
}

export interface SalaryMonthStatus {
  month: string;
  label: string;
  status: "unpaid" | "part_paid" | "paid" | "none";
  payable_paise: number;
  paid_paise: number;
  balance_paise: number;
  finalized: boolean;
  computed_paise: number;
  month_open: boolean;
}

export interface SalaryMonthsResult {
  applicable: boolean;
  months: SalaryMonthStatus[];
  default_month: string;
  current_month: string;
}

export interface CommissionCandidate extends InvoiceLine {
  booking_id: string;
  total_paise: number;
  settled_paise: number;
  selected: boolean;
  selectable: boolean;
  not_set?: boolean;
  plan_covered?: boolean;
}

export interface PayPreview {
  captain_id: string;
  captain_name?: string | null;
  kind: "salary" | "commission";
  lines: InvoiceLine[];
  gross_paise: number;
  due_paise: number;
  can_pay: boolean;
  default_amount_paise: number;
  warnings: string[];
  /** salary */
  month?: string;
  month_label?: string;
  previously_paid_paise?: number;
  finalized?: boolean;
  computed_paise?: number;
  stale?: boolean;
  month_open?: boolean;
  status?: "unpaid" | "part_paid" | "paid" | "none";
  invoices?: string[];
  /** commission */
  period_from?: string;
  period_to?: string;
  candidates?: CommissionCandidate[];
  not_set?: CommissionCandidate[];
}

export interface InvoicePayInput {
  kind: InvoiceKind;
  month?: string;
  from_date?: string;
  to_date?: string;
  booking_ids?: string[];
  amount_paise?: number;
  expected_gross_paise?: number;
  reason?: string;
  method: PaymentMethod;
  paid_on?: string;
  note?: string;
  idempotency_key: string;
  cash_offset_paise?: number;
}

export interface ReconcileResult {
  ok: boolean;
  captains_checked: number;
  invoices_checked: number;
  truncated: boolean;
  mismatches: { captain_id: string | null; check: string; expected_paise: number; actual_paise: number; detail?: string }[];
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

  /** §7.15 — settle bookings that were paid another way (no money moves), and undo it. */
  markPaid: (id: string, payload: { booking_ids?: string[]; month?: string; reason?: string }) =>
    apiClient.post<ApiSuccess<{ count: number; amount_paise: number }>>(`/payroll/captains/${id}/commissions/mark-paid`, payload).then(withMessage),
  markUnpaid: (id: string, payload: { booking_ids: string[]; reason: string }) =>
    apiClient.post<ApiSuccess<{ count: number; amount_paise: number }>>(`/payroll/captains/${id}/commissions/mark-unpaid`, payload).then(withMessage),
  cash: (id: string) => apiClient.get<ApiSuccess<CashPosition>>(`/payroll/captains/${id}/cash`).then((r) => r.data.data),
  cashReceived: (id: string, payload: { amount_paise: number; booking_id?: string; note?: string; idempotency_key: string }) =>
    apiClient.post<ApiSuccess<CashReceipt>>(`/payroll/captains/${id}/cash-received`, payload).then(withMessage),
  voidCashReceipt: (receiptId: string, reason: string) => apiClient.post<ApiSuccess<CashReceipt>>(`/payroll/cash-receipts/${receiptId}/void`, { reason }).then(withMessage),
  bookingCaptainMoney: (bookingId: string) => apiClient.get<ApiSuccess<BookingCaptainMoney>>(`/payroll/bookings/${bookingId}/captain-money`).then((r) => r.data.data),

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
  payPreview: (id: string, params: { kind: "salary" | "commission"; month?: string; from?: string; to?: string }) =>
    apiClient.get<ApiSuccess<PayPreview>>(`/payroll/captains/${id}/pay-preview`, { params: clean({ ...params }) }).then((r) => r.data.data),
  salaryMonths: (id: string) => apiClient.get<ApiSuccess<SalaryMonthsResult>>(`/payroll/captains/${id}/salary-months`).then((r) => r.data.data),
  payInvoice: (id: string, payload: InvoicePayInput) =>
    apiClient.post<ApiSuccess<PayrollInvoice>>(`/payroll/captains/${id}/pay-invoice`, payload).then(withMessage),
  invoices: (filters: InvoiceListFilters) => apiClient.get<InvoicePage>("/payroll/invoices", { params: clean({ ...filters }) }).then((r) => r.data),
  invoice: (id: string) => apiClient.get<ApiSuccess<PayrollInvoice>>(`/payroll/invoices/${id}`).then((r) => r.data.data),
  voidInvoice: (id: string, reason: string) => apiClient.post<ApiSuccess<PayrollInvoice>>(`/payroll/invoices/${id}/void`, { reason }).then(withMessage),
  reconcile: (center_id?: string) => apiClient.get<ApiSuccess<ReconcileResult>>("/payroll/reconcile", { params: clean({ center_id }) }).then((r) => r.data.data),
  myInvoices: (page = 1) => apiClient.get<InvoicePage>("/payroll/me/invoices", { params: { page, page_size: 30 } }).then((r) => r.data),
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
