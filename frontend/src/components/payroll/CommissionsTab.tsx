import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Pencil } from "lucide-react";
import { Badge, Button, EmptyState, ErrorState, Input, Modal } from "../ui";
import { payrollApi, type CommissionRow, type PayrollCaptainDetail } from "../../api/payroll";
import { getErrorMessage } from "../../lib/api-client";
import { asUtcInstant, formatDateTime } from "../../lib/date";
import { cleanAmountInput, rupeesInput, rupeesText, toPaise } from "../../lib/paise";
import { cn } from "../../lib/cn";
import { Banner, SkeletonRows, monthLabel } from "./payrollUi";
import { InvoiceLink } from "./InvoiceLink";
import { usePayrollWrite } from "./usePayrollWrite";

/** Completed bookings with the commission each earned; edit one with a reason. */
export function CommissionsTab({ detail, month }: { detail: PayrollCaptainDetail; month: string }) {
  const [editing, setEditing] = useState<CommissionRow | null>(null);
  const query = useQuery({ queryKey: ["payroll-commissions", detail.id, month], queryFn: () => payrollApi.commissions(detail.id, month) });

  if (query.isLoading) return <SkeletonRows rows={4} />;
  if (query.isError) return <ErrorState message="Couldn't load commissions." busy={query.isFetching} onRetry={() => void query.refetch()} />;
  const rows = query.data || [];
  const total = rows.reduce((s, r) => s + r.amount_paise, 0);
  const unset = rows.filter((r) => r.not_set).length;
  const unsetPlan = rows.filter((r) => r.not_set && r.plan_covered).length;

  return (
    <div className="space-y-3">
      {detail.pay.basis === "legacy" && <Banner>Commission starts once you set this captain's pay terms. Work done before that earns the old per-job fee.</Banner>}
      {detail.pay.basis === "salary" && <Banner>This captain is on a salary, so bookings earn no commission.</Banner>}
      {unset > 0 && (
        <Banner tone="warning">
          {unset} {unset === 1 ? "booking has" : "bookings have"} no commission set yet. Set the amount so the captain is credited.
          {unsetPlan > 0 && ` ${unsetPlan} of them ${unsetPlan === 1 ? "was" : "were"} washes covered by a plan — a plan wash's commission is always set by you.`}
        </Banner>
      )}
      <p className="text-sm text-[var(--color-text-secondary)]">
        {monthLabel(month)}: <span className="font-mono-num font-semibold text-[var(--color-text-primary)]">{rupeesText(total)}</span> from {rows.length} {rows.length === 1 ? "booking" : "bookings"}
      </p>

      {!rows.length ? (
        <EmptyState title="No Completed Bookings" description="Commission shows up here as soon as this captain completes a booking." />
      ) : (
        <ul className="space-y-2">
          {rows.map((r) => (
            <li key={r.id} className={cn("rounded-xl border p-3", r.not_set ? "border-amber-300 bg-amber-50/60" : "border-gray-100")}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="flex flex-wrap items-center gap-2 text-sm font-semibold text-[var(--color-text-primary)]">
                    <span className="font-mono-num">{r.booking_number}</span>
                    {r.edited && <Badge tone="info">Edited</Badge>}
                    {r.plan_covered && <Badge tone="info">Plan Wash</Badge>}
                    {r.not_set && <Badge tone="warning">Commission Not Set</Badge>}
                    <PaymentState row={r} />
                  </p>
                  <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">
                    {[r.customer_name, [r.vehicle_type, r.service_name].filter(Boolean).join(" · "), r.completed_at ? formatDateTime(asUtcInstant(r.completed_at)) : ""].filter(Boolean).join(" · ")}
                  </p>
                  {r.reason && <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">Reason: {r.reason}</p>}
                </div>
                <Button size="sm" variant="outline" aria-label={`Edit commission for ${r.booking_number}`} onClick={() => setEditing(r)}>
                  <Pencil className="h-3.5 w-3.5" /> {r.not_set ? "Set Amount" : "Edit"}
                </Button>
              </div>
              <dl className="mt-2 grid grid-cols-3 gap-2 text-xs">
                <Stat label="Booking Value" value={rupeesText(r.base_paise)} />
                <Stat label="Rate" value={r.rate_percent != null ? `${r.rate_percent}%` : "—"} />
                <Stat label="Commission" value={r.not_set ? "Not Set" : rupeesText(r.amount_paise)} strong />
              </dl>
            </li>
          ))}
        </ul>
      )}

      <Modal open={!!editing} onClose={() => setEditing(null)} title={editing ? `Commission — ${editing.booking_number}` : "Commission"}>
        {editing && <EditForm row={editing} onClose={() => setEditing(null)} />}
      </Modal>
    </div>
  );
}

function PaymentState({ row }: { row: CommissionRow }) {
  if (row.payment_state === "paid") {
    return (
      <Badge tone="success">
        Paid — {row.invoice_numbers?.length ? <InvoiceLink numbers={row.invoice_numbers} ids={row.invoice_ids} /> : "Invoiced"}
      </Badge>
    );
  }
  if (row.payment_state === "part_paid") return <Badge tone="warning">Part Paid — {rupeesText(row.unsettled_paise || 0)} Unpaid</Badge>;
  if (row.payment_state === "unpaid") return <Badge tone="neutral">Unpaid</Badge>;
  return null;
}

function Stat({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div>
      <dt className="text-[var(--color-text-secondary)]">{label}</dt>
      <dd className={cn("font-mono-num text-[var(--color-text-primary)]", strong && "font-semibold")}>{value}</dd>
    </div>
  );
}

function EditForm({ row, onClose }: { row: CommissionRow; onClose: () => void }) {
  const write = usePayrollWrite();
  const [by, setBy] = useState<"amount" | "percent">("amount");
  const plan = !!row.plan_covered;
  const extras = plan ? Math.max(0, (row.auto_paise ?? 0) - (row.plan_wash_paise ?? 0)) : 0;
  const [amount, setAmount] = useState(rupeesInput(plan ? (row.plan_wash_paise ?? 0) : row.amount_paise));
  const [percent, setPercent] = useState(row.rate_percent != null ? String(row.rate_percent) : "");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");

  const amountPaise = by === "amount" ? toPaise(amount) : Math.round(((Number(percent) || 0) / 100) * (plan ? row.plan_base_paise || row.base_paise : row.base_paise));
  const maxPct = row.max_percent;
  const preview = amountPaise !== null && amountPaise >= 0 ? amountPaise : null;

  const mutation = useMutation({
    mutationFn: () => payrollApi.editCommission(row.booking_id, by === "amount" ? { amount_paise: amountPaise!, reason: reason.trim() } : { percent: Number(percent), reason: reason.trim() }),
    onSuccess: (res) => {
      write.ok("Commission Saved", res.message);
      onClose();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        setError("");
        if (by === "amount") {
          if (amountPaise === null) return setError("Enter the commission in whole rupees.");
        } else if (percent.trim() === "" || !Number.isFinite(Number(percent)) || Number(percent) < 0) {
          return setError("Enter a percent between 0 and the limit.");
        }
        if (preview !== null && preview + extras > row.max_paise) return setError(`That is above the limit — at most ${maxPct}% of the booking, ${rupeesText(row.max_paise)}.`);
        if (reason.trim().length < 3) return setError("A reason is required for every change.");
        if (!mutation.isPending) mutation.mutate();
      }}
    >
      {plan && (
        <Banner>
          A plan covered this wash, so its commission isn't worked out for you — enter what the captain earns for the wash itself
          {extras > 0 ? `. The ${rupeesText(extras)} for paid extras on this booking is added on top automatically` : ""}.
        </Banner>
      )}
      <p className="text-sm text-[var(--color-text-secondary)]">
        Booking value <span className="font-mono-num font-semibold text-[var(--color-text-primary)]">{rupeesText(row.base_paise)}</span>
        {" · "}
        <span className="font-semibold text-[var(--color-text-primary)]">
          Max {maxPct}% = {rupeesText(row.max_paise)}
        </span>
      </p>
      <div role="radiogroup" aria-label="Set commission by" className="flex gap-2">
        {(["amount", "percent"] as const).map((k) => (
          <button
            key={k}
            type="button"
            role="radio"
            aria-checked={by === k}
            onClick={() => setBy(k)}
            className={cn("flex min-h-11 flex-1 items-center justify-center rounded-xl border-2 px-3 text-sm font-semibold", by === k ? "border-[#0A66F0] bg-[#E8F0FE] text-[#0E1A33]" : "border-gray-200 text-[#5F6878] hover:border-gray-400")}
          >
            {k === "amount" ? "Amount (₹)" : "Percent (%)"}
          </button>
        ))}
      </div>
      {by === "amount" ? (
        <Input label={plan ? "Commission For The Wash (₹)" : "Commission (₹)"} inputMode="numeric" value={amount} onChange={(e) => setAmount(cleanAmountInput(e.target.value))} autoFocus />
      ) : (
        <Input label={`Commission Percent (Max ${maxPct})`} inputMode="decimal" value={percent} onChange={(e) => setPercent(e.target.value.replace(/[^\d.]/g, "").slice(0, 5))} autoFocus />
      )}
      {preview !== null && (
        <p className="-mt-2 text-xs text-[var(--color-text-secondary)]">
          The captain gets <span className="font-mono-num font-semibold text-[var(--color-text-primary)]">{rupeesText(preview + extras)}</span> on this booking.
        </p>
      )}
      <Input label="Reason" maxLength={200} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Extra heavy cleaning" />
      {error && (
        <p role="alert" className="text-sm text-[var(--color-error)]">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="button" variant="outline" className="flex-1" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" className="flex-1" isLoading={mutation.isPending}>
          {row.not_set ? "Set Amount" : "Save Commission"}
        </Button>
      </div>
    </form>
  );
}
