import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2 } from "lucide-react";
import { Badge, Button, ErrorState, Input, Modal, Select, Switch } from "../ui";
import { newIdempotencyKey, payrollApi, type CommissionCandidate, type InvoiceKind, type PayPreview, type PayrollCaptainDetail, type PayrollInvoice, type PaymentMethod } from "../../api/payroll";
import { useAuth } from "../../context/AuthContext";
import { useConfirm } from "../../context/ConfirmContext";
import { getErrorMessage } from "../../lib/api-client";
import { format, todayIST } from "../../lib/date";
import { cleanAmountInput, rupeesInWords, rupeesInput, toPaise } from "../../lib/paise";
import { cn } from "../../lib/cn";
import { INVOICE_KIND_LABEL } from "./InvoiceDocument";
import { invoicePath } from "./InvoiceLink";
import { rupeesExact } from "./money";
import { Banner, FRANCHISE_BANNER, SkeletonRows, monthLabel } from "./payrollUi";
import { usePayrollWrite } from "./usePayrollWrite";

const DEFAULT_LARGE_PAISE = 5_000_000;
const METHODS: { value: PaymentMethod; label: string }[] = [
  { value: "cash", label: "Cash" },
  { value: "upi", label: "UPI" },
  { value: "bank", label: "Bank" },
  { value: "other", label: "Other" },
];
type Preset = "today" | "yesterday" | "this_week" | "last_week" | "custom";
const PRESETS: { key: Preset; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "yesterday", label: "Yesterday" },
  { key: "this_week", label: "This Week" },
  { key: "last_week", label: "Last Week" },
  { key: "custom", label: "Custom" },
];

function shiftDay(day: string, days: number): string {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}
/** Monday of the week containing `day`. */
function mondayOf(day: string): string {
  const wd = (new Date(`${day}T12:00:00Z`).getUTCDay() + 6) % 7;
  return shiftDay(day, -wd);
}
export function presetRange(p: Exclude<Preset, "custom">): { from: string; to: string } {
  const today = todayIST();
  if (p === "today") return { from: today, to: today };
  if (p === "yesterday") return { from: shiftDay(today, -1), to: shiftDay(today, -1) };
  if (p === "this_week") return { from: mondayOf(today), to: today };
  const lastMon = shiftDay(mondayOf(today), -7);
  return { from: lastMon, to: shiftDay(lastMon, 6) };
}

type CaptainLite = Pick<PayrollCaptainDetail, "id" | "full_name" | "dues_paise" | "finance_impact" | "pay"> & { cash_held_paise?: number; alert_payment_paise?: number };

/**
 * Pay a captain: choose a salary month or a commission period, see exactly
 * what goes on the invoice, adjust the amount (a reason is needed when it
 * differs), then pay — and the invoice opens. Advance / other payments use the
 * simpler Record Payment form.
 */
export function PayWizard({ captain, open, onClose, onOther }: { captain: CaptainLite; open: boolean; onClose: () => void; onOther?: () => void }) {
  return (
    <Modal open={open} onClose={onClose} title={`Pay — ${captain.full_name}`} maxWidth="max-w-3xl">
      <WizardBody captain={captain} onClose={onClose} onOther={onOther} />
    </Modal>
  );
}

function WizardBody({ captain, onClose, onOther }: { captain: CaptainLite; onClose: () => void; onOther?: () => void }) {
  const { user } = useAuth();
  const confirm = useConfirm();
  const write = usePayrollWrite();
  const [key] = useState(newIdempotencyKey);
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [kind, setKind] = useState<Extract<InvoiceKind, "salary" | "commission">>(captain.pay.basis === "commission" ? "commission" : "salary");
  const [month, setMonth] = useState("");
  const [preset, setPreset] = useState<Preset>("today");
  const [custom, setCustom] = useState({ from: todayIST(), to: todayIST() });
  const [unticked, setUnticked] = useState<Set<string>>(new Set());
  const [amount, setAmount] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [method, setMethod] = useState<PaymentMethod>("cash");
  const [paidOn, setPaidOn] = useState(todayIST());
  const [note, setNote] = useState("");
  const [settle, setSettle] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<PayrollInvoice | null>(null);

  const monthsQuery = useQuery({ queryKey: ["payroll-salary-months", captain.id], queryFn: () => payrollApi.salaryMonths(captain.id), enabled: kind === "salary" });
  const chosenMonth = month || monthsQuery.data?.default_month || "";
  const range = preset === "custom" ? custom : presetRange(preset);
  const rangeOk = !!range.from && !!range.to && range.from <= range.to && range.to <= todayIST();

  const previewQuery = useQuery({
    queryKey: ["payroll-pay-preview", captain.id, kind, kind === "salary" ? chosenMonth : `${range.from}_${range.to}`],
    queryFn: () => payrollApi.payPreview(captain.id, kind === "salary" ? { kind, month: chosenMonth } : { kind, from: range.from, to: range.to }),
    enabled: step >= 2 && (kind === "salary" ? !!chosenMonth : rangeOk),
    staleTime: 0,
    gcTime: 0,
  });
  const preview = previewQuery.data;

  const candidates: CommissionCandidate[] = preview?.candidates || [];
  const selected = candidates.filter((c) => c.selectable && !unticked.has(c.booking_id));
  const dueOf = (p: PayPreview | undefined) => (!p ? 0 : kind === "commission" ? selected.reduce((s, c) => s + c.amount_paise, 0) : p.due_paise);
  const due = dueOf(preview);
  const amountPaise = amount === null ? due : toPaise(amount);
  const differs = amountPaise !== null && amountPaise !== due;
  const cashHeld = Math.max(0, captain.cash_held_paise || 0);
  const offset = settle && amountPaise ? Math.min(cashHeld, amountPaise) : 0;
  const largeAt = captain.alert_payment_paise || DEFAULT_LARGE_PAISE;

  const mutation = useMutation({
    mutationFn: () =>
      payrollApi.payInvoice(captain.id, {
        kind,
        ...(kind === "salary" ? { month: chosenMonth } : { from_date: range.from, to_date: range.to, booking_ids: selected.map((c) => c.booking_id) }),
        amount_paise: differs ? amountPaise! : undefined,
        expected_gross_paise: due,
        reason: differs ? reason.trim() : undefined,
        method,
        paid_on: paidOn,
        note: note.trim() || undefined,
        cash_offset_paise: offset || undefined,
        idempotency_key: key,
      }),
    onSuccess: (res) => {
      write.ok("Payment Recorded", `${res.message} — ${res.data.number}`);
      setDone(res.data);
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const nextFromStep1 = () => {
    setError("");
    if (kind === "salary" && !chosenMonth) return setError("Pick the salary month.");
    if (kind === "commission" && !rangeOk) return setError("Pick a period that ends today or earlier.");
    setUnticked(new Set());
    setAmount(null);
    setStep(2);
  };
  const nextFromStep2 = () => {
    setError("");
    if (!preview?.can_pay && kind === "salary") return setError("There is nothing left to pay for this month.");
    if (kind === "commission" && !selected.length) return setError("Tick at least one booking.");
    if (kind === "commission" && amount === null && due <= 0) return setError("These changes add up to nothing to pay now. Untick the lines that reduce it, or leave them — they come off the next invoice.");
    if (amountPaise === null || amountPaise <= 0) return setError("Enter an amount in whole rupees, more than ₹0.");
    if (differs && reason.trim().length < 3) return setError(`The invoice comes to ${rupeesExact(due)} — say why you are paying ${rupeesExact(amountPaise)} instead.`);
    setStep(3);
  };
  const submit = async () => {
    setError("");
    if (!paidOn || paidOn > todayIST()) return setError("Pick today's date or an earlier one.");
    if ((amountPaise || 0) >= largeAt) {
      const ok = await confirm({
        title: `Pay ${rupeesExact(amountPaise!)} To ${captain.full_name}?`,
        message: `${rupeesInWords((amountPaise || 0) / 100)}. This is a large payment and the admin is notified.`,
        confirmLabel: "Yes, Pay It",
        tone: "default",
      });
      if (!ok) return;
    }
    mutation.mutate();
  };

  const steps = ["What To Pay", "Review", "Pay"];

  if (done) {
    return (
      <div className="space-y-4 text-center">
        <CheckCircle2 className="mx-auto h-12 w-12 text-green-600" />
        <div>
          <p className="text-lg font-bold text-[var(--color-text-primary)]">{rupeesExact(done.paid_paise)} Paid</p>
          <p className="text-sm text-[var(--color-text-secondary)]">
            Invoice <span className="font-mono-num font-semibold">{done.number}</span> was created and the captain was told.
          </p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row">
          <Button variant="outline" className="flex-1" onClick={onClose}>
            Close
          </Button>
          <Link to={invoicePath(user?.role, done.id)} className="flex-1">
            <Button className="w-full">Open Invoice</Button>
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <ol className="flex items-center gap-2 text-xs font-semibold" aria-label="Steps">
        {steps.map((s, i) => (
          <li key={s} aria-current={step === i + 1 ? "step" : undefined} className={cn("flex items-center gap-1.5 rounded-full px-3 py-1", step === i + 1 ? "bg-[#E8F0FE] text-[#0A66F0]" : "text-[#5F6878]")}>
            <span className="font-mono-num">{i + 1}</span> {s}
          </li>
        ))}
      </ol>
      {!captain.finance_impact && <Banner>{FRANCHISE_BANNER}</Banner>}

      {step === 1 && (
        <div className="space-y-4">
          <div role="radiogroup" aria-label="What to pay" className="flex gap-2">
            {(["salary", "commission"] as const).map((k) => (
              <button
                key={k}
                type="button"
                role="radio"
                aria-checked={kind === k}
                onClick={() => setKind(k)}
                className={cn("flex min-h-11 flex-1 items-center justify-center rounded-xl border-2 px-3 text-sm font-semibold", kind === k ? "border-[#0A66F0] bg-[#E8F0FE] text-[#0E1A33]" : "border-gray-200 text-[#5F6878] hover:border-gray-400")}
              >
                {k === "salary" ? "Salary Month" : "Commission Period"}
              </button>
            ))}
          </div>

          {kind === "salary" ? (
            monthsQuery.isLoading ? (
              <SkeletonRows rows={3} />
            ) : monthsQuery.isError ? (
              <ErrorState message="Couldn't load the months." busy={monthsQuery.isFetching} onRetry={() => void monthsQuery.refetch()} />
            ) : !monthsQuery.data?.applicable ? (
              <Banner tone="warning">This captain isn't on a salary, so there are no salary months to pay. Use Commission Period, or Advance / Other.</Banner>
            ) : (
              <ul className="divide-y divide-gray-100 rounded-xl border border-gray-100">
                {monthsQuery.data.months.map((m) => (
                  <li key={m.month}>
                    <button
                      type="button"
                      onClick={() => setMonth(m.month)}
                      aria-pressed={chosenMonth === m.month}
                      className={cn("flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left text-sm", chosenMonth === m.month && "bg-[#E8F0FE]")}
                    >
                      <span className="font-medium text-[var(--color-text-primary)]">
                        {m.label}
                        {m.month_open && <span className="ml-1.5 text-xs font-normal text-[var(--color-text-secondary)]">still running</span>}
                      </span>
                      <span className="flex items-center gap-2">
                        <span className="font-mono-num text-xs text-[var(--color-text-secondary)]">{m.payable_paise ? rupeesExact(m.balance_paise) + " due" : "Nothing yet"}</span>
                        <Badge tone={m.status === "paid" ? "success" : m.status === "part_paid" ? "warning" : "neutral"}>
                          {m.status === "paid" ? "Paid" : m.status === "part_paid" ? "Part Paid" : m.status === "unpaid" ? "Unpaid" : "No Salary"}
                        </Badge>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )
          ) : (
            <div className="space-y-3">
              <div role="radiogroup" aria-label="Period" className="flex flex-wrap gap-1.5">
                {PRESETS.map((p) => (
                  <button
                    key={p.key}
                    type="button"
                    role="radio"
                    aria-checked={preset === p.key}
                    onClick={() => setPreset(p.key)}
                    className={cn("rounded-full border px-3 py-1.5 text-xs font-semibold", preset === p.key ? "border-[#0A66F0] bg-[#E8F0FE] text-[#0A66F0]" : "border-[#E4E9F1] text-[#5F6878] hover:border-gray-400")}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
              {preset === "custom" ? (
                <div className="grid grid-cols-2 gap-3">
                  <Input type="date" label="From" value={custom.from} max={todayIST()} onChange={(e) => setCustom({ ...custom, from: e.target.value })} />
                  <Input type="date" label="To" value={custom.to} max={todayIST()} onChange={(e) => setCustom({ ...custom, to: e.target.value })} />
                </div>
              ) : (
                <p className="text-sm text-[var(--color-text-secondary)]">
                  {format(range.from)}
                  {range.to !== range.from ? ` – ${format(range.to)}` : ""}
                </p>
              )}
              <p className="text-xs text-[var(--color-text-secondary)]">Bookings already on an earlier invoice are left out, so nothing is paid twice.</p>
            </div>
          )}
          {error && (
            <p role="alert" className="text-sm text-[var(--color-error)]">
              {error}
            </p>
          )}
          <div className="flex flex-col gap-2 sm:flex-row sm:justify-between">
            {onOther ? (
              <Button variant="ghost" onClick={onOther}>
                Advance Or Other Payment
              </Button>
            ) : (
              <span />
            )}
            <Button onClick={nextFromStep1} disabled={kind === "salary" && !monthsQuery.data?.applicable}>
              Review
            </Button>
          </div>
        </div>
      )}

      {step === 2 && (
        <div className="space-y-4">
          {previewQuery.isLoading ? (
            <SkeletonRows rows={4} />
          ) : previewQuery.isError || !preview ? (
            <ErrorState message={previewQuery.error ? getErrorMessage(previewQuery.error) : "Couldn't load the invoice."} busy={previewQuery.isFetching} onRetry={() => void previewQuery.refetch()} />
          ) : (
            <>
              <p className="text-sm font-semibold text-[var(--color-text-primary)]">
                {kind === "salary" ? `Salary — ${monthLabel(preview.month || chosenMonth)}` : `Commission — ${format(preview.period_from || range.from)}${preview.period_to && preview.period_to !== preview.period_from ? ` – ${format(preview.period_to)}` : ""}`}
              </p>
              {preview.warnings.map((w) => (
                <Banner key={w} tone="warning">
                  {w}
                </Banner>
              ))}
              {kind === "salary" ? <SalaryLines preview={preview} /> : <CommissionLines preview={preview} unticked={unticked} setUnticked={(next) => { setUnticked(next); setAmount(null); }} />}

              <div className="rounded-xl bg-[var(--color-surface)] p-3 text-sm">
                <div className="flex justify-between">
                  <span className="text-[var(--color-text-secondary)]">{kind === "salary" ? "Balance For This Month" : "Invoice Total"}</span>
                  <span className="font-mono-num font-semibold">{rupeesExact(due)}</span>
                </div>
              </div>
              <Input
                label="Amount To Pay (₹)"
                inputMode="numeric"
                value={amount ?? rupeesInput(due)}
                onChange={(e) => setAmount(cleanAmountInput(e.target.value))}
                hint={due % 100 !== 0 && amount === null ? `Exactly ${rupeesExact(due)} (with paise) is paid unless you change this.` : undefined}
              />
              {amountPaise !== null && amountPaise > 0 && <p className="-mt-2 text-xs text-[var(--color-text-secondary)]">{rupeesInWords(amountPaise / 100)}</p>}
              {differs && (
                <>
                  <Banner tone="warning">
                    {amountPaise !== null && amountPaise < due
                      ? kind === "salary"
                        ? "Paying less than the balance is a part payment — the rest stays due for this month."
                        : "Paying less than the invoice total deducts the difference. To pay only some bookings, untick them instead."
                      : "Paying more than the invoice total adds the extra as a bonus."}
                  </Banner>
                  <Input label="Reason For The Difference" maxLength={200} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Part payment now, rest on Friday" />
                </>
              )}
            </>
          )}
          {error && (
            <p role="alert" className="text-sm text-[var(--color-error)]">
              {error}
            </p>
          )}
          <div className="flex gap-2">
            <Button variant="outline" className="flex-1" onClick={() => setStep(1)}>
              Back
            </Button>
            <Button className="flex-1" onClick={nextFromStep2} disabled={!preview || (kind === "salary" && !preview.can_pay)}>
              Continue
            </Button>
          </div>
        </div>
      )}

      {step === 3 && (
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (!mutation.isPending) void submit();
          }}
        >
          <div className="rounded-xl bg-[var(--color-surface)] p-3 text-sm">
            Paying <span className="font-mono-num font-semibold">{rupeesExact(amountPaise || 0)}</span> to <span className="font-semibold">{captain.full_name}</span> for{" "}
            {INVOICE_KIND_LABEL[kind].toLowerCase()} {kind === "salary" ? `(${monthLabel(chosenMonth)})` : `(${format(range.from)}${range.to !== range.from ? ` – ${format(range.to)}` : ""})`}.
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Select label="Paid By" value={method} onChange={(e) => setMethod(e.target.value as PaymentMethod)}>
              {METHODS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
            <Input type="date" label="Paid On" value={paidOn} max={todayIST()} onChange={(e) => setPaidOn(e.target.value)} />
          </div>
          <Input label="Note (Optional)" maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Transferred to the captain's account" />
          {cashHeld > 0 && (
            <div className="space-y-2">
              <Switch checked={settle} onChange={setSettle} label="Settle Cash Held" description={`This captain is holding ${rupeesExact(cashHeld)} of customer cash. Switch on to set it off against this payment.`} />
              {settle && !!amountPaise && (
                <p className="text-xs text-[var(--color-text-secondary)]">
                  {rupeesExact(offset)} is settled against cash held, so you hand over <span className="font-mono-num font-semibold text-[var(--color-text-primary)]">{rupeesExact(amountPaise - offset)}</span>.
                </p>
              )}
            </div>
          )}
          {error && (
            <p role="alert" className="text-sm text-[var(--color-error)]">
              {error}
            </p>
          )}
          <div className="flex gap-2">
            <Button type="button" variant="outline" className="flex-1" onClick={() => setStep(2)}>
              Back
            </Button>
            <Button type="submit" className="flex-1" isLoading={mutation.isPending}>
              Pay And Create Invoice
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}

function SalaryLines({ preview }: { preview: PayPreview }) {
  return (
    <ul className="divide-y divide-gray-100 rounded-xl border border-gray-100 text-sm">
      {preview.lines.map((l, i) => (
        <li key={i} className="flex items-start justify-between gap-3 px-3 py-2">
          <div className="min-w-0">
            <p className="font-medium text-[var(--color-text-primary)]">{l.label || l.type}</p>
            {l.type === "salary" && (
              <p className="text-xs text-[var(--color-text-secondary)]">
                {l.expected_working_days} working days · present {l.present_days}
                {l.half_days ? ` + ${l.half_days} half` : ""} · leave {l.leave_days} ({l.paid_leave_days} paid) · unpaid {l.unpaid_days} · holidays {l.holidays}
              </p>
            )}
          </div>
          <span className="font-mono-num font-semibold">{rupeesExact(l.amount_paise)}</span>
        </li>
      ))}
      {!!preview.previously_paid_paise && (
        <li className="flex justify-between gap-3 px-3 py-2 text-[var(--color-text-secondary)]">
          <span>Already Paid {preview.invoices?.length ? `(${preview.invoices.join(", ")})` : ""}</span>
          <span className="font-mono-num">− {rupeesExact(preview.previously_paid_paise)}</span>
        </li>
      )}
    </ul>
  );
}

function CommissionLines({ preview, unticked, setUnticked }: { preview: PayPreview; unticked: Set<string>; setUnticked: (s: Set<string>) => void }) {
  const candidates = useMemo(() => preview.candidates || [], [preview]);
  const toggle = (id: string) => {
    const next = new Set(unticked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setUnticked(next);
  };
  return (
    <div className="space-y-2">
      {!candidates.length ? (
        <Banner>No unpaid commission in this period.</Banner>
      ) : (
        <ul className="divide-y divide-gray-100 rounded-xl border border-gray-100 text-sm">
          {candidates.map((c) => (
            <li key={c.booking_id}>
              <label className="flex cursor-pointer items-start gap-3 px-3 py-2.5">
                <input type="checkbox" className="mt-1 h-4 w-4" checked={!unticked.has(c.booking_id)} onChange={() => toggle(c.booking_id)} aria-label={`Include booking ${c.booking_number}`} />
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-1.5 font-medium text-[var(--color-text-primary)]">
                    <span className="font-mono-num">{c.booking_number}</span>
                    {c.kind === "revised" && <Badge tone="warning">Revised</Badge>}
                  </span>
                  <span className="block text-xs text-[var(--color-text-secondary)]">
                    {[c.date ? format(c.date) : null, [c.vehicle_type, c.service_name].filter(Boolean).join(" · ")].filter(Boolean).join(" · ")}
                  </span>
                </span>
                <span className={cn("font-mono-num font-semibold", c.amount_paise < 0 && "text-[var(--color-error)]")}>{rupeesExact(c.amount_paise)}</span>
              </label>
            </li>
          ))}
        </ul>
      )}
      {!!preview.not_set?.length && (
        <div className="rounded-xl border border-amber-300 bg-amber-50/60 p-3 text-sm text-amber-800">
          <p className="font-semibold">Commission Not Set — Left Off This Invoice</p>
          <ul className="mt-1 space-y-0.5 text-xs">
            {preview.not_set.map((c) => (
              <li key={c.booking_id}>
                <span className="font-mono-num">{c.booking_number}</span> {c.plan_covered ? "· plan wash" : ""} — set its amount in the Commissions tab, then it can be paid.
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
