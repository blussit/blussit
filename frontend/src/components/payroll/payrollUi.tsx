import type { ReactNode } from "react";
import { ChevronLeft, ChevronRight, Info } from "lucide-react";
import { Badge } from "../ui";
import { cn } from "../../lib/cn";
import { rupeesText } from "../../lib/paise";
import { todayIST } from "../../lib/date";
import type { AttendanceStatus, LedgerKind, PayBasis, PayTerms } from "../../api/payroll";

export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export const BASIS_LABEL: Record<PayBasis, string> = { salary: "Salary", commission: "Commission", legacy: "Legacy" };

export function BasisBadge({ basis }: { basis: PayBasis }) {
  return <Badge tone={basis === "legacy" ? "warning" : basis === "salary" ? "info" : "success"}>{BASIS_LABEL[basis] || basis}</Badge>;
}

/** "₹15,000/mo" or "20% per booking" — the terms in one short line. */
export function termsLine(pay: Pick<PayTerms, "basis" | "salary_monthly_paise" | "commission">): string {
  if (pay.basis === "salary") return `${rupeesText(pay.salary_monthly_paise)}/mo`;
  if (pay.basis === "commission") {
    return pay.commission.mode === "per_booking" ? "Set Per Booking" : `${pay.commission.percent}% per booking`;
  }
  return "Per-job fee (legacy)";
}

/** Dues are earned minus paid: negative means the captain was paid ahead (advance) or a reversal came after payout. */
export function Dues({ paise, className }: { paise: number; className?: string }) {
  if (paise < 0) {
    return (
      <span className={cn("font-mono-num font-semibold text-[var(--color-error)]", className)}>
        {rupeesText(Math.abs(paise))}
        <span className="ml-1 text-[11px] font-medium">Recovery Due</span>
      </span>
    );
  }
  return <span className={cn("font-mono-num font-semibold", paise > 0 ? "text-amber-700" : "text-[var(--ui-ink,#0E1A33)]", className)}>{rupeesText(paise)}</span>;
}

export const LEDGER_LABEL: Record<LedgerKind, string> = {
  commission: "Commission",
  commission_adjust: "Commission Change",
  salary_accrual: "Salary",
  allowance: "Allowance",
  bonus: "Bonus",
  deduction: "Deduction",
  advance: "Advance",
  payout: "Payment",
  payout_void: "Payment Voided",
  deposit_received: "Deposit Received",
  deposit_refund: "Deposit Refunded",
  deposit_forfeit: "Deposit Forfeited",
  cash_offset: "Cash Settled",
};

export const ATTENDANCE_LABEL: Record<AttendanceStatus, string> = {
  present: "Present",
  half_day: "Half Day",
  leave: "Leave",
  absent: "Absent",
  weekly_off: "Weekly Off",
  holiday: "Holiday",
  not_joined: "Not Joined Yet",
  left: "Left",
  upcoming: "Upcoming",
  pending: "Not Marked Yet",
};

export const ATTENDANCE_TONE: Record<AttendanceStatus, "success" | "warning" | "info" | "error" | "neutral"> = {
  present: "success",
  half_day: "warning",
  leave: "info",
  absent: "error",
  weekly_off: "neutral",
  holiday: "neutral",
  not_joined: "neutral",
  left: "neutral",
  upcoming: "neutral",
  pending: "warning",
};

/** Same hairline-underlined tab strip the captain profile uses. */
export function TabStrip<T extends string>({ tabs, value, onChange }: { tabs: readonly { key: T; label: string; badge?: boolean }[]; value: T; onChange: (key: T) => void }) {
  return (
    <div role="tablist" className="-mx-1 flex gap-1 overflow-x-auto border-b border-gray-100 px-1">
      {tabs.map((t) => (
        <button
          key={t.key}
          type="button"
          role="tab"
          aria-selected={value === t.key}
          onClick={() => onChange(t.key)}
          className={cn(
            "flex shrink-0 items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium transition-colors",
            value === t.key ? "border-[#0A66F0] text-[#0A66F0]" : "border-transparent text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]",
          )}
        >
          {t.label}
          {t.badge && <span className="h-2 w-2 rounded-full bg-amber-500" />}
        </button>
      ))}
    </div>
  );
}

export const monthKey = (day: string) => day.slice(0, 7);
export const currentMonth = () => monthKey(todayIST());

/** "2026-11" -> "November 2026". */
export function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number);
  if (!y || !m) return month;
  return new Date(Date.UTC(y, m - 1, 15)).toLocaleDateString("en-IN", { month: "long", year: "numeric", timeZone: "UTC" });
}

export function shiftMonth(month: string, delta: number): string {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(Date.UTC(y, m - 1 + delta, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

/** Previous / next month with the name between — never past this month. */
export function MonthPicker({ value, onChange, className }: { value: string; onChange: (month: string) => void; className?: string }) {
  const atCurrent = value >= currentMonth();
  return (
    <div className={cn("inline-flex h-11 items-center rounded-[12px] border border-[#E4E9F1] bg-white", className)}>
      <button type="button" aria-label="Previous Month" onClick={() => onChange(shiftMonth(value, -1))} className="flex h-full w-10 items-center justify-center rounded-l-[12px] text-[#5F6878] hover:bg-[#F3F6FA]">
        <ChevronLeft className="h-4 w-4" />
      </button>
      <span className="min-w-[8.5rem] px-1 text-center text-sm font-medium text-[#0E1A33]">{monthLabel(value)}</span>
      <button
        type="button"
        aria-label="Next Month"
        disabled={atCurrent}
        onClick={() => onChange(shiftMonth(value, 1))}
        className="flex h-full w-10 items-center justify-center rounded-r-[12px] text-[#5F6878] hover:bg-[#F3F6FA] disabled:cursor-not-allowed disabled:opacity-40"
      >
        <ChevronRight className="h-4 w-4" />
      </button>
    </div>
  );
}

/** A calm one-line notice: neutral, amber or red. No new colours. */
export function Banner({ tone = "info", children }: { tone?: "info" | "warning" | "error"; children: ReactNode }) {
  const cls = {
    info: "border-[#E4E9F1] bg-[#F7F9FC] text-[#5F6878]",
    warning: "border-amber-200 bg-amber-50/60 text-amber-800",
    error: "border-red-200 bg-red-50 text-[var(--color-error)]",
  }[tone];
  return (
    <div role={tone === "error" ? "alert" : undefined} className={cn("flex items-start gap-2 rounded-xl border px-3 py-2.5 text-sm", cls)}>
      <Info className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export const FRANCHISE_BANNER = "Paid by franchise — not counted in Blussit Finance";

/** A label over a value, the way the detail tabs show facts. */
export function Fact({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <div className={cn("rounded-xl border border-gray-100 p-3", className)}>
      <p className="text-[11px] font-medium text-[var(--color-text-secondary)]">{label}</p>
      <div className="mt-0.5 text-base font-semibold text-[var(--color-text-primary)]">{children}</div>
    </div>
  );
}

export function SkeletonRows({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-busy="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-12 animate-pulse rounded-xl bg-[#EEF3FA]" />
      ))}
    </div>
  );
}
