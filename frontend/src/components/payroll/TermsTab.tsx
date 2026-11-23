import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button, Input, Switch } from "../ui";
import { PAY_LIMITS, payrollApi, type CommissionMode, type PayHistorySegment, type PayrollCaptainDetail, type TermsInput } from "../../api/payroll";
import { getErrorMessage } from "../../lib/api-client";
import { format, todayIST } from "../../lib/date";
import { cleanAmountInput, rupeesInput, rupeesText, toPaise } from "../../lib/paise";
import { ChoicePair, MAX_COMMISSION_PERCENT, WeekdayChips } from "./PayFields";
import { Banner, BASIS_LABEL, termsLine } from "./payrollUi";
import { usePayrollWrite } from "./usePayrollWrite";

/** Edit the pay terms (effective from a date) and see how they changed over time. */
export function TermsTab({ detail }: { detail: PayrollCaptainDetail }) {
  const write = usePayrollWrite();
  const p = detail.pay;
  const wasLegacy = p.basis === "legacy";
  const maxPct = detail.max_commission_percent || MAX_COMMISSION_PERCENT;
  const [basis, setBasis] = useState<"" | "salary" | "commission">(p.basis === "legacy" ? "" : p.basis);
  const [salary, setSalary] = useState(rupeesInput(p.salary_monthly_paise));
  const [weeklyOff, setWeeklyOff] = useState<number[]>(p.weekly_off_days ?? [5, 6]);
  const [leaves, setLeaves] = useState(String(p.paid_leaves_per_month ?? 0));
  const [petrol, setPetrol] = useState(rupeesInput(p.petrol_per_day_paise));
  const [extraDay, setExtraDay] = useState(!!p.extra_day_on_off);
  const [mode, setMode] = useState<CommissionMode>(p.commission?.mode || "percent");
  const [percent, setPercent] = useState(p.commission?.percent ? String(p.commission.percent) : "");
  const [from, setFrom] = useState(todayIST());
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");

  const mutation = useMutation({
    mutationFn: (payload: TermsInput) => payrollApi.saveTerms(detail.id, payload),
    onSuccess: (res) => {
      write.ok("Pay Terms Saved", res.message);
      setReason("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const submit = () => {
    setError("");
    if (!basis) return setError("Choose Salary or Commission.");
    if (!from) return setError("Pick the date these terms start from.");
    if (!wasLegacy && reason.trim().length < 3) return setError("A reason is required when you change pay terms.");
    const payload: TermsInput = { basis, effective_from: from, reason: reason.trim() || undefined };
    if (basis === "salary") {
      const s = toPaise(salary);
      if (!s || s <= 0) return setError("Enter the monthly salary in rupees.");
      const l = Number(leaves);
      if (!Number.isInteger(l) || l < 0 || l > PAY_LIMITS.paidLeavesMax) return setError(`Paid leaves per month must be a whole number from 0 to ${PAY_LIMITS.paidLeavesMax}.`);
      if (petrol.trim() && toPaise(petrol) === null) return setError("Petrol per day must be a whole number of rupees.");
      Object.assign(payload, { salary_monthly_paise: s, weekly_off_days: weeklyOff, paid_leaves_per_month: l, petrol_per_day_paise: toPaise(petrol) || 0, extra_day_on_off: extraDay });
    } else {
      const pct = Number(percent);
      if (mode === "percent" && (percent.trim() === "" || !Number.isFinite(pct) || pct < 0 || pct > maxPct)) return setError(`Enter a commission percent from 0 to ${maxPct}.`);
      payload.commission = { mode, percent: mode === "percent" ? pct : 0 };
    }
    mutation.mutate(payload);
  };

  const history = [...(detail.pay_history || [])].sort((a, b) => (a.effective_from < b.effective_from ? 1 : -1));

  return (
    <div className="grid gap-5 md:grid-cols-[minmax(0,1fr)_minmax(0,18rem)]">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (!mutation.isPending) submit();
        }}
      >
        <p className="text-sm text-[var(--color-text-secondary)]">
          Now: <span className="font-semibold text-[var(--color-text-primary)]">{BASIS_LABEL[p.basis]}</span> · {termsLine(p)}. A change applies from the date below — nothing already earned is rewritten.
        </p>
        {wasLegacy && <Banner tone="warning">Set the pay basis to stop the old per-job fee for new work. Work already done is not touched.</Banner>}

        <ChoicePair
          label="Pay Basis"
          value={basis}
          options={[
            { value: "salary", label: "Salary" },
            { value: "commission", label: "Commission" },
          ]}
          onChange={setBasis}
        />

        {basis === "salary" && (
          <div className="space-y-3">
            <Input label="Monthly Salary (₹)" inputMode="numeric" value={salary} onChange={(e) => setSalary(cleanAmountInput(e.target.value))} />
            <div className="grid grid-cols-1 gap-3">
              <WeekdayChips value={weeklyOff} onChange={setWeeklyOff} />
              <Input label="Paid Leaves Per Month" inputMode="numeric" value={leaves} onChange={(e) => setLeaves(cleanAmountInput(e.target.value).slice(0, 2))} />
            </div>
            <Input label="Petrol Per Day (₹, Optional)" inputMode="numeric" value={petrol} onChange={(e) => setPetrol(cleanAmountInput(e.target.value))} hint="Added for every day the captain is present." />
            <Switch checked={extraDay} onChange={setExtraDay} label="Pay An Extra Day For Working The Off Day" description="Off by default — a weekly off worked is not paid extra." />
          </div>
        )}

        {basis === "commission" && (
          <div className="space-y-3">
            <ChoicePair
              label="Commission Type"
              value={mode}
              options={[
                { value: "percent", label: "Fixed Percent" },
                { value: "per_booking", label: "Per Booking" },
              ]}
              onChange={setMode}
            />
            {mode === "percent" ? (
              <Input
                label={`Commission Percent (0–${maxPct})`}
                inputMode="decimal"
                value={percent}
                onChange={(e) => setPercent(e.target.value.replace(/[^\d.]/g, "").slice(0, 5))}
                hint="Applies to bookings completed from the date below. Past bookings keep the rate they earned."
              />
            ) : (
              <Banner>You set the commission on each booking. Until you do, it shows as "Commission Not Set".</Banner>
            )}
          </div>
        )}

        <Input type="date" label="Effective From" value={from} min={shiftDay(todayIST(), -60)} max={shiftDay(todayIST(), 30)} onChange={(e) => setFrom(e.target.value)} hint="Up to two months back, or a month ahead." />
        <Input label={wasLegacy ? "Reason (Optional)" : "Reason For The Change"} maxLength={200} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Yearly raise" />
        {error && (
          <p role="alert" className="text-sm text-[var(--color-error)]">
            {error}
          </p>
        )}
        <Button type="submit" className="w-full sm:w-auto" isLoading={mutation.isPending}>
          Save Terms
        </Button>
      </form>

      <div>
        <p className="mb-2 text-sm font-semibold text-[var(--color-text-primary)]">Pay History</p>
        {!history.length ? (
          <p className="text-sm text-[var(--color-text-secondary)]">No changes yet.</p>
        ) : (
          <ol className="space-y-3 border-l-2 border-[#E4E9F1] pl-4">
            {history.map((h, i) => (
              <li key={`${h.effective_from}-${i}`} className="relative text-sm">
                <span className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-white bg-[#0A66F0]" />
                <p className="font-semibold text-[var(--color-text-primary)]">From {format(h.effective_from)}</p>
                <p className="text-[var(--color-text-secondary)]">{describeSegment(h)}</p>
                {h.reason && <p className="text-xs text-[var(--color-text-secondary)]">Reason: {h.reason}</p>}
                {h.actor_name && <p className="text-xs text-[var(--color-text-secondary)]">By {h.actor_name}</p>}
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}

/** A day shifted by `days` (IST calendar arithmetic on the YYYY-MM-DD string). */
function shiftDay(day: string, days: number): string {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function describeSegment(h: PayHistorySegment): string {
  const t = h.terms;
  const basis = t?.basis ?? h.basis;
  if (basis === "salary") return `Salary ${rupeesText(t?.salary_monthly_paise)}/mo`;
  if (basis === "commission" && t?.commission) return t.commission.mode === "per_booking" ? "Commission set per booking" : `Commission ${t.commission.percent}%`;
  return basis ? BASIS_LABEL[basis] : "Terms changed";
}
