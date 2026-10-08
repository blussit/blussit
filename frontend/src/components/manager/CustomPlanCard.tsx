import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Banknote, CalendarPlus, Check, Copy, Link2, Pencil, RefreshCw, Send, Undo2, X } from "lucide-react";
import { CUSTOM_PLAN_STATUS_LABELS, customPlanApi, type CustomPlan, type CustomPlanCar, type CustomPlanStatus } from "../../api/customPlans";
import { useConfirm } from "../../context/ConfirmContext";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage, getErrorStatus } from "../../lib/api-client";
import { asUtcInstant, formatDateTime } from "../../lib/date";
import { toTitle } from "../../lib/titleCase";
import { Badge, Button } from "../ui";
import { dayLabel, ExtendPassDialog, extensionLine, type ExtendTarget } from "../society/PassExtension";
import { RefundCarDialog, type RefundTarget } from "./RefundCarDialog";
import { customPlanTotals } from "../../lib/customPlanTotals";

/**
 * One custom multi-car plan (a manager's cart): customer, every car with
 * its washes (counts before payment, "x of y left" after), the price, the
 * open payment link, and — while unpaid — Send Link / Mark Cash Paid /
 * Revise / Cancel. A plan that came back `needs_review` (a car already had
 * another plan when it was paid) names the skipped car and the refund due.
 * A paid plan: Renew Plan (a new cart for the next 30 days — each car starts
 * the day after its current pass) and Refund Car (unused washes to the
 * customer's wallet). Renewal carts link both ways.
 */

const STATUS_TONE: Record<CustomPlanStatus, "neutral" | "warning" | "success" | "error" | "info"> = {
  draft: "neutral",
  awaiting_payment: "warning",
  activating: "info",
  active: "success",
  needs_review: "error",
  refunded: "neutral",
  cancelled: "neutral",
};

/** "6 Nov 2026" — the server now sends labels unpadded; stripping a
 *  leading zero stays as a harmless guard for older responses. */
const dayText = (label?: string | null) => (label || "").replace(/^0(\d)/, "$1");

/** "#4F2A1C" — enough of a cart id to tell carts apart in a link. */
const shortId = (id?: string | null) => (id ? `#${id.slice(-6).toUpperCase()}` : "");

export const isOpenPlan = (p: CustomPlan) => p.status === "draft" || p.status === "awaiting_payment";

const rupees = (n?: number | null) => `₹${Math.round(Number(n || 0)).toLocaleString("en-IN")}`;

function CarBlock({
  car,
  index,
  plan,
  canExtend,
  onExtend,
  onRefund,
}: {
  car: CustomPlanCar;
  index: number;
  plan: CustomPlan;
  canExtend: boolean;
  onExtend: (t: ExtendTarget) => void;
  onRefund?: (t: RefundTarget) => void;
}) {
  const pass = car.subscription;
  const paid = !!pass;
  const ext = extensionLine(pass?.extension_days, pass?.extended_until);
  const refunded = car.status === "refunded";
  const scheduled = pass?.status === "scheduled";
  const refundable = Math.floor(Number(car.refundable_amount || 0));
  // An unpaid renewal: when this car's new 30 days would start if paid now.
  const newPeriod = isOpenPlan(plan) && plan.renewal_of && car.starts_on_label ? dayText(car.starts_on_label) : "";
  return (
    <li className="px-3.5 py-3" data-testid="custom-plan-car">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-black">
            <span className="font-mono-num">{car.registration_number || "Car"}</span>
            {car.vehicle_type_name ? <span className="font-normal text-gray-500"> · {toTitle(car.vehicle_type_name)}</span> : null}
          </p>
          {scheduled ? (
            <p className="text-xs font-semibold text-black" data-testid="custom-plan-car-starts">
              Starts {dayText(car.starts_on_label) || dayLabel(pass?.start_date)}
            </p>
          ) : pass?.end_date && !refunded ? (
            <p className="text-xs text-gray-500">
              {pass.start_date ? `${dayLabel(pass.start_date)} – ` : "Until "}
              {dayLabel(pass.end_date)}
              {pass.status && pass.status !== "active" ? ` · ${toTitle(pass.status)}` : ""}
            </p>
          ) : null}
          {newPeriod && (
            <p className="text-xs font-semibold text-black" data-testid="custom-plan-car-new-period">
              New Period Starts {newPeriod}
            </p>
          )}
          {ext && !refunded && <p className="text-xs font-semibold text-black">{ext}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {refunded ? (
            <Badge tone="neutral">Refunded {rupees(car.refund?.amount)}</Badge>
          ) : car.status === "skipped" ? (
            <Badge tone="error">Skipped</Badge>
          ) : scheduled ? (
            <Badge tone="warning">Scheduled</Badge>
          ) : plan.status !== "cancelled" && !isOpenPlan(plan) && paid ? (
            <Badge tone="success">Active</Badge>
          ) : null}
          <span className="font-mono-num text-sm font-semibold text-black">{rupees(car.amount ?? car.price)}</span>
        </div>
      </div>
      <ul className="mt-1.5 space-y-0.5">
        {car.items.map((i) => (
          <li key={i.service_id} className="flex items-center justify-between gap-3 text-sm">
            <span className="min-w-0 text-gray-700">
              {paid && !refunded ? toTitle(i.service_name) : `${i.count} × ${toTitle(i.service_name)}`}
            </span>
            <span className="shrink-0 font-mono-num text-xs text-gray-500">
              {refunded ? "" : paid ? `${i.remaining ?? 0} of ${i.count} left` : `${rupees(i.unit_price)} each`}
            </span>
          </li>
        ))}
      </ul>
      {refunded && car.refund && (
        <p className="mt-1.5 text-xs text-gray-500" data-testid="custom-plan-car-refund">
          {[
            car.refund.washes ? `${car.refund.washes} Unused Wash${car.refund.washes === 1 ? "" : "es"}` : "",
            car.refund.reason ? `“${car.refund.reason}”` : "",
            car.refund.at ? formatDateTime(asUtcInstant(car.refund.at)) : "",
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
      )}
      {car.status === "skipped" && (
        <p className="mt-1.5 rounded-lg bg-red-50 px-2.5 py-1.5 text-xs text-red-800">
          {car.note || "Already had another active plan when this was paid."}
          {car.refund_due ? <span className="font-semibold"> Refund Due {rupees(car.refund_due)}.</span> : null}
        </p>
      )}
      {canExtend && pass?.can_extend && (
        <Button
          size="sm"
          variant="outline"
          className="mt-2 min-h-11 sm:min-h-0"
          onClick={() =>
            onExtend({
              subscriptionId: pass.id,
              plate: car.registration_number || "This car",
              planName: "Custom Plan",
              remaining: pass.remaining,
              endDate: pass.end_date,
              daysUsed: pass.extension_days ?? 0,
              daysLeft: pass.extension_days_left ?? 10 - (pass.extension_days ?? 0),
            })
          }
        >
          <CalendarPlus className="h-3.5 w-3.5" /> Extend Pass
        </Button>
      )}
      {onRefund && refundable > 0 && !refunded && (
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-dashed border-gray-200 px-2.5 py-1.5" data-testid="custom-plan-car-refundable">
          <span className="text-xs text-gray-600">
            {car.status === "skipped" ? "Its Share" : "Unused Washes Worth"} <span className="font-mono-num font-semibold text-black">{rupees(refundable)}</span>
          </span>
          <Button size="sm" variant="outline" className="min-h-11 sm:min-h-0" onClick={() => onRefund({ plan, car, index })}>
            <Undo2 className="h-3.5 w-3.5" /> Refund Car
          </Button>
        </div>
      )}
    </li>
  );
}

export function CustomPlanCard({
  plan,
  canAct,
  centerName,
  onOpenCustomer,
  onRevise,
  onRenew,
  onOpenPlan,
  onChanged,
  adminActions = false,
}: {
  plan: CustomPlan;
  /** Manager: Send Link / Mark Cash / Revise / Cancel / Extend / Renew / Refund. */
  canAct: boolean;
  /** Admin view (otherwise read-only): Renew Plan and Refund Car on a paid
   *  cart, and finishing a renewal it started (link / cash / revise / cancel). */
  adminActions?: boolean;
  centerName?: string;
  onOpenCustomer?: (customerId: string) => void;
  onRevise?: (plan: CustomPlan) => void;
  /** Paid plan → open the builder prefilled for a renewal. */
  onRenew?: (plan: CustomPlan) => void;
  /** Open another cart (the renewal / the plan it renews). */
  onOpenPlan?: (planId: string) => void;
  onChanged?: (plan: CustomPlan) => void;
}) {
  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const confirm = useConfirm();
  const [copied, setCopied] = useState(false);
  const [extendFor, setExtendFor] = useState<ExtendTarget | null>(null);
  const [refundFor, setRefundFor] = useState<RefundTarget | null>(null);

  const refresh = (next?: CustomPlan) => {
    queryClient.invalidateQueries({ queryKey: ["custom-plans"] });
    if (next) {
      queryClient.setQueryData(["custom-plan", next.id], next);
      onChanged?.(next);
    } else {
      queryClient.invalidateQueries({ queryKey: ["custom-plan", plan.id] });
    }
  };
  const fail = (title: string) => (err: unknown) => {
    // 409: someone revised it meanwhile — show the fresh copy.
    if (getErrorStatus(err) === 409) refresh();
    pushToast({ tone: "error", title, message: getErrorMessage(err) });
  };

  const link = useMutation({
    mutationFn: (sendWhatsapp: boolean) => customPlanApi.sendLink(plan.id, plan.revision, sendWhatsapp),
    onSuccess: (r, sendWhatsapp) => {
      refresh(r.custom_plan);
      pushToast({
        tone: "success",
        title: sendWhatsapp ? "Payment Link Sent" : "Payment Link Ready",
        message: sendWhatsapp ? `${rupees(r.amount)} — sent on WhatsApp.` : "Copy it below and share it.",
      });
    },
    onError: fail("Couldn't Make The Link"),
  });
  const cash = useMutation({
    mutationFn: () => customPlanApi.markCash(plan.id, plan.revision),
    onSuccess: ({ plan: next, message }) => {
      refresh(next);
      queryClient.invalidateQueries({ queryKey: ["center-subscription-overview"] });
      pushToast({ tone: next.status === "active" ? "success" : "warning", title: next.status === "active" ? "Plan Active" : "Needs Review", message });
    },
    onError: fail("Couldn't Mark Paid"),
  });
  const cancel = useMutation({
    mutationFn: () => customPlanApi.cancel(plan.id),
    onSuccess: (next) => {
      refresh(next);
      pushToast({ tone: "success", title: "Plan Cancelled", message: "Any link sent for it no longer works." });
    },
    onError: fail("Couldn't Cancel"),
  });

  const copy = async () => {
    if (!plan.payment_link?.short_url) return;
    try {
      await navigator.clipboard.writeText(plan.payment_link.short_url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard blocked — the link is still visible to select by hand
    }
  };

  const open = isOpenPlan(plan);
  const busy = link.isPending || cash.isPending || cancel.isPending;
  const paidPlan = plan.status === "active" || plan.status === "needs_review";
  // Header counts leave refunded cars out; money shows "Paid · Refunded".
  const totals = customPlanTotals(plan);
  const paidActs = canAct || adminActions;
  const openActs = canAct || (adminActions && !!plan.renewal_of);
  const planLink = (id: string, label: string, testId: string) =>
    onOpenPlan ? (
      <button
        type="button"
        onClick={() => onOpenPlan(id)}
        className="inline-flex min-h-11 items-center gap-1 text-xs font-semibold text-black underline decoration-gray-300 decoration-2 underline-offset-2 hover:decoration-black sm:min-h-0"
        data-testid={testId}
      >
        <Link2 className="h-3.5 w-3.5" /> {label}
      </button>
    ) : (
      <span className="inline-flex items-center gap-1 text-xs font-semibold text-black" data-testid={testId}>
        <Link2 className="h-3.5 w-3.5" /> {label}
      </span>
    );
  return (
    <article className="rounded-2xl border border-[var(--color-card-border)] bg-white" data-testid="custom-plan-card">
      <header className="flex flex-wrap items-start justify-between gap-2 border-b border-[var(--color-card-border)] px-3.5 py-3">
        <div className="min-w-0">
          {onOpenCustomer ? (
            <button
              type="button"
              onClick={() => onOpenCustomer(plan.customer_id)}
              className="max-w-full truncate text-left font-semibold text-black underline decoration-gray-300 decoration-2 underline-offset-2 hover:decoration-black"
            >
              {plan.customer_name || "Customer"}
            </button>
          ) : (
            <p className="font-semibold text-black">{plan.customer_name || "Customer"}</p>
          )}
          <p className="text-xs text-gray-500">
            {[plan.customer_phone, centerName, plan.created_at ? formatDateTime(asUtcInstant(plan.created_at)) : ""].filter(Boolean).join(" · ")}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <Badge tone={STATUS_TONE[plan.status] || "neutral"}>{CUSTOM_PLAN_STATUS_LABELS[plan.status] || toTitle(plan.status)}</Badge>
          {plan.renewal_of && <Badge tone="info">Renewal</Badge>}
        </div>
      </header>
      {(plan.renewal_of || plan.renewal_cart_id) && (
        <div className="flex flex-wrap gap-x-4 border-b border-[var(--color-card-border)] px-3.5 py-1.5">
          {plan.renewal_of && planLink(plan.renewal_of, `Renewal Of Plan ${shortId(plan.renewal_of)}`, "custom-plan-renewal-of")}
          {plan.renewal_cart_id && planLink(plan.renewal_cart_id, `Renewed By Plan ${shortId(plan.renewal_cart_id)}`, "custom-plan-renewed-by")}
        </div>
      )}

      <div className="px-3.5 pt-3">
        <p className="text-sm text-black">
          <span className="font-semibold" data-testid="custom-plan-counts">
            {totals.cars} Car{totals.cars === 1 ? "" : "s"} · {totals.washes} Wash{totals.washes === 1 ? "" : "es"} · {plan.period_days || 30} Days
          </span>
          {totals.refundedCars > 0 && totals.refundedCars < plan.cars.length && (
            <span className="text-xs text-gray-500"> · {totals.refundedCars} {totals.refundedCars === 1 ? "Car" : "Cars"} Refunded</span>
          )}
        </p>
        <p className="mt-0.5 text-sm">
          <span className="font-mono-num text-lg font-bold text-black">{rupees(plan.total_amount)}</span>
          {totals.refunded > 0 && (
            // Paid stays paid; refunds sit beside it ("₹1,499 Paid · ₹300 Refunded").
            <span className="ml-1 text-xs font-semibold text-gray-600" data-testid="custom-plan-refunded">
              Paid · {rupees(totals.refunded)} Refunded
            </span>
          )}
          {plan.discount_amount > 0 && (
            <span className="ml-2 text-xs text-gray-500">
              <span className="line-through">{rupees(plan.subtotal)}</span> · {rupees(plan.discount_amount)} off
            </span>
          )}
        </p>
        {plan.period_start && plan.period_end && (
          <p className="text-xs text-gray-500">
            {dayLabel(plan.period_start)} – {dayLabel(plan.period_end)}
          </p>
        )}
      </div>

      {plan.status === "needs_review" && (
        <div className="mx-3.5 mt-3 flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-sm text-red-800" data-testid="custom-plan-review">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            {plan.review?.skipped?.length ? (
              <>
                <span className="font-semibold">Skipped {plan.review.skipped.map((p) => p || "a car").join(", ")}</span> — already on another plan.{" "}
              </>
            ) : null}
            {plan.review?.refund_due ? <span className="font-semibold">Refund Due {rupees(plan.review.refund_due)}. </span> : null}
            {plan.review?.reason || "Sort it out with the customer."}
          </span>
        </div>
      )}

      <ul className="mt-2 divide-y divide-[var(--ui-row-line,#EEF2F7)] border-t border-[var(--color-card-border)]">
        {plan.cars.map((car, i) => (
          <CarBlock
            key={car.vehicle_id || car.registration_number || i}
            car={car}
            index={i}
            plan={plan}
            canExtend={canAct}
            onExtend={setExtendFor}
            onRefund={paidActs && paidPlan ? setRefundFor : undefined}
          />
        ))}
      </ul>

      {plan.payment && (
        <p className="border-t border-[var(--color-card-border)] px-3.5 py-2.5 text-xs text-gray-600">
          <Check className="mr-1 inline h-3.5 w-3.5 text-[var(--color-success)]" />
          Paid {rupees(plan.payment.amount)} {plan.payment.method === "cash" ? "in cash" : "online"}
          {plan.payment.at ? ` · ${formatDateTime(asUtcInstant(plan.payment.at))}` : ""}
        </p>
      )}
      {plan.note && <p className="border-t border-[var(--color-card-border)] px-3.5 py-2.5 text-xs text-gray-600">&ldquo;{plan.note}&rdquo;</p>}
      {plan.status === "cancelled" && plan.cancel_reason && (
        <p className="border-t border-[var(--color-card-border)] px-3.5 py-2.5 text-xs text-gray-600">Cancelled: {plan.cancel_reason}</p>
      )}

      {open && plan.payment_link?.short_url && (
        <div className="border-t border-[var(--color-card-border)] px-3.5 py-3">
          <p className="mb-1.5 text-xs font-semibold text-gray-500">Payment Link · {rupees(plan.payment_link.amount)}</p>
          <div className="flex items-center gap-2 rounded-xl border border-[var(--color-card-border)] bg-gray-50 px-3 py-2">
            <span className="min-w-0 flex-1 truncate font-mono-num text-sm text-black">{plan.payment_link.short_url}</span>
            <Button size="sm" variant="outline" className="min-h-11 sm:min-h-0" onClick={copy}>
              <Copy className="h-3.5 w-3.5" /> {copied ? "Copied" : "Copy"}
            </Button>
          </div>
          <p className="mt-1.5 text-xs text-gray-500">Paying it starts every car&apos;s pass by itself.</p>
        </div>
      )}

      {openActs && open && (
        <div className="grid grid-cols-2 gap-2 whitespace-nowrap border-t border-[var(--color-card-border)] px-3.5 py-3">
          <Button className="col-span-2 min-h-11" disabled={busy} isLoading={link.isPending} onClick={() => link.mutate(true)}>
            <Send className="h-4 w-4" /> {plan.payment_link ? "Resend Payment Link" : "Send Payment Link"}
          </Button>
          <Button
            variant="outline"
            className="min-h-11"
            disabled={busy}
            isLoading={cash.isPending}
            onClick={async () => {
              const ok = await confirm({
                title: "Mark Cash Paid?",
                message: `${rupees(plan.total_amount)} collected in cash from ${plan.customer_name || "the customer"} — every car's 30 days start today and the cash is recorded against you.`,
                confirmLabel: "Yes, Activate",
              });
              if (ok) cash.mutate();
            }}
          >
            <Banknote className="hidden h-4 w-4 sm:inline" /> Mark Cash Paid
          </Button>
          {!plan.payment_link && (
            <Button variant="outline" className="min-h-11" disabled={busy} onClick={() => link.mutate(false)}>
              <Copy className="hidden h-4 w-4 sm:inline" /> Get Link Only
            </Button>
          )}
          {onRevise && (
            <Button variant="ghost" className="min-h-11" disabled={busy} onClick={() => onRevise(plan)}>
              <Pencil className="h-4 w-4" /> Revise
            </Button>
          )}
          <Button
            variant="ghost"
            className="min-h-11 text-[var(--color-error)]"
            disabled={busy}
            isLoading={cancel.isPending}
            onClick={async () => {
              const ok = await confirm({
                title: "Cancel This Plan?",
                message: plan.payment_link ? "The payment link stops working. The customer can no longer pay it." : "Nothing was charged.",
                tone: "danger",
                confirmLabel: "Cancel Plan",
              });
              if (ok) cancel.mutate();
            }}
          >
            <X className="h-4 w-4" /> Cancel
          </Button>
        </div>
      )}
      {paidActs && paidPlan && onRenew && (
        <div className="border-t border-[var(--color-card-border)] px-3.5 py-3">
          {plan.renewal_cart_id ? (
            onOpenPlan && (
              <Button variant="outline" className="min-h-11 w-full" onClick={() => onOpenPlan(plan.renewal_cart_id!)}>
                <RefreshCw className="h-4 w-4" /> Open Renewal
              </Button>
            )
          ) : (
            <Button variant="outline" className="min-h-11 w-full" onClick={() => onRenew(plan)} data-testid="custom-plan-renew">
              <RefreshCw className="h-4 w-4" /> Renew Plan
            </Button>
          )}
        </div>
      )}
      <ExtendPassDialog target={extendFor} onClose={() => setExtendFor(null)} />
      <RefundCarDialog target={refundFor} onClose={() => setRefundFor(null)} onDone={(next) => onChanged?.(next)} />
    </article>
  );
}
