import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, IndianRupee } from "lucide-react";
import { paymentApi, type CollectionsReport as BaseReport } from "../../api/payment";
import { Button, Card, CardBody, CardHeader, ErrorState, Input, Modal } from "../ui";
import { getErrorMessage } from "../../lib/api-client";
import { formatDateTime, todayIST } from "../../lib/date";
import { carAndService, toTitle } from "../../lib/titleCase";

/**
 * The money-acknowledgment ledger, shared by both oversight levels:
 * manager (one row per CAPTAIN of their center) and admin (one row per
 * CENTER, plus subscription revenue and the needs-attention queue).
 * Cash vs online vs UNCOLLECTED per row — uncollected on a completed
 * booking is the surveillance number: someone finished a wash and no
 * payment was recorded.
 */
type PresetKey = "today" | "week" | "month" | "30d" | "custom";

/** Money fields the wallet / custom-plan backend added (2026-10-07) on top
 *  of the shared CollectionsReport type. */
type WalletMoney = { wallet_amount?: number; billed_amount?: number };
/** MONEY-2 (2026-10-07): tips by their own method, manager paybacks, and
 *  what the center really kept (cash + online − paid back). */
type PaybackMoney = { tips_cash?: number; tips_online?: number; paid_back_by_managers?: number; net_collected?: number };
type PaidBackLine = { amount: number; count: number; wallet_amount: number; goodwill_amount: number; cash_amount: number; online_amount: number };
type Row = BaseReport["rows"][number] & PaybackMoney;
type Report = Omit<BaseReport, "totals" | "rows"> & {
  rows: Row[];
  totals: BaseReport["totals"] & WalletMoney & PaybackMoney;
  /** Money managers paid back to customers in the range (center: theirs;
   *  admin: every center). */
  paid_back_by_managers?: PaidBackLine | null;
  /** Admin roll-up: custom multi-car plans sold (link or cash). */
  custom_plans?: { online_amount: number; cash_amount: number; count: number; cash_count: number };
  /** Admin roll-up: customer wallets right now (not range-bound). */
  wallet?: {
    credits_held: number;
    credit_customers: number;
    dues_outstanding: number;
    due_customers: number;
    payouts_amount: number;
    payouts_count: number;
  };
};

const PRESETS: { key: PresetKey; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "week", label: "This Week" },
  { key: "month", label: "This Month" },
  { key: "30d", label: "Last 30 Days" },
];

/** YYYY-MM-DD `days` before an IST calendar day (pure date maths at UTC
 *  noon, so no timezone or DST can shift the day). */
function shiftDay(day: string, days: number): string {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

/** Ranges the way a settlement conversation actually works: today, the week
 *  so far (Monday-based), the calendar month so far — all IST business days
 *  (the old UTC dates were a day behind until 05:30 IST). */
function presetRange(key: PresetKey): { from: string; to: string } {
  const today = todayIST();
  if (key === "today") return { from: today, to: today };
  if (key === "week") {
    const weekday = new Date(`${today}T12:00:00Z`).getUTCDay(); // 0 = Sunday
    return { from: shiftDay(today, (weekday + 6) % 7), to: today };
  }
  if (key === "month") {
    return { from: `${today.slice(0, 8)}01`, to: today };
  }
  return { from: "", to: "" }; // 30d is the backend default
}

export function CollectionsReportCard({
  title,
  entityLabel,
  queryKey,
  fetcher,
}: {
  title: string;
  entityLabel: string;
  queryKey: string;
  fetcher: (params: { date_from?: string; date_to?: string }) => Promise<BaseReport>;
}) {
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [preset, setPreset] = useState<PresetKey>("30d");

  const applyPreset = (key: PresetKey) => {
    setPreset(key);
    const { from, to } = presetRange(key);
    setDateFrom(from);
    setDateTo(to);
  };

  const { data, isLoading, isError, isFetching, refetch } = useQuery({
    queryKey: [queryKey, dateFrom, dateTo],
    queryFn: () => fetcher({ date_from: dateFrom || undefined, date_to: dateTo || undefined }) as Promise<Report>,
  });

  const report = data as Report | undefined;
  const rows: Row[] = report?.rows || [];
  const t = report?.totals;
  const back = report?.paid_back_by_managers;
  // Paid back / net columns only when a manager paid something back.
  const showBack = !!t?.paid_back_by_managers;
  const money = (n?: number | null) => `₹${Math.round(Number(n || 0)).toLocaleString("en-IN")}`;
  // Only when a manager discounted or a tip came in during the range.
  const showExtras = !!t?.manager_discount_amount || !!t?.tip_amount;

  return (
    <Card>
      <CardHeader className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <IndianRupee className="h-4 w-4 text-[var(--color-primary)]" />
          <h2 className="font-semibold text-[var(--color-text-primary)]">{title}</h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* Settlement happens by day, week and month — one tap each; the
              date boxes stay for anything else. */}
          <div className="flex flex-wrap gap-1.5">
            {PRESETS.map((option) => (
              <button
                key={option.key}
                type="button"
                onClick={() => applyPreset(option.key)}
                aria-pressed={preset === option.key}
                className={`rounded-lg border px-2.5 py-1.5 text-xs font-semibold transition-colors ${
                  preset === option.key ? "border-[var(--color-primary)] bg-[var(--color-primary-light)] text-[var(--color-primary)]" : "border-[var(--color-card-border)] text-[var(--color-text-secondary)] hover:border-gray-400"
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Input
              type="date"
              value={dateFrom}
              max={dateTo || undefined}
              onChange={(e) => { setDateFrom(e.target.value); setPreset("custom"); }}
              placeholder="From"
            />
            <Input
              type="date"
              value={dateTo}
              min={dateFrom || undefined}
              onChange={(e) => { setDateTo(e.target.value); setPreset("custom"); }}
              placeholder="To"
            />
          </div>
        </div>
      </CardHeader>
      <CardBody>
        <p
          className="mb-3 text-xs text-[var(--color-text-secondary)]"
          title={'Paid bookings only. "Uncollected" = completed washes with no payment recorded yet.'}
        >
          {PRESETS.find((option) => option.key === preset)?.label || "Selected Range"}
        </p>
        {isLoading ? (
          <p className="text-sm text-[var(--color-text-secondary)]">Loading…</p>
        ) : isError && !data ? (
          <ErrorState message="Couldn't load the collections." busy={isFetching} onRetry={() => void refetch()} />
        ) : !rows.length ? (
          <p className="text-sm text-[var(--color-text-secondary)]">No collections in this period.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-left text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-xs text-[var(--color-text-secondary)]">
                  <th className="py-2 pr-3 font-medium">{entityLabel}</th>
                  <th className="py-2 pr-3 font-medium">Washes</th>
                  <th className="py-2 pr-3 font-medium">Plan Washes</th>
                  <th className="py-2 pr-3 font-medium">Cash</th>
                  <th className="py-2 pr-3 font-medium">Online</th>
                  <th className="py-2 pr-3 font-medium">Uncollected</th>
                  {showExtras && <th className="py-2 pr-3 font-medium">Discount / Tip</th>}
                  {showBack && <th className="py-2 pr-3 font-medium">Paid Back</th>}
                  {showBack && <th className="py-2 font-medium">Net</th>}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.captain_id || r.service_center_id || "none"} className="border-b border-gray-50 last:border-0">
                    <td className="py-2.5 pr-3">
                      <span className="font-medium text-[var(--color-text-primary)]">{(r.captain_id && r.captain_id !== "manager" ? r.captain_name : toTitle(r.captain_name)) || r.center_name || "—"}</span>
                      {r.employee_id && <span className="ml-1.5 whitespace-nowrap rounded-full bg-[var(--ui-icon-bg,#000)] px-1.5 py-0.5 font-mono-num text-[9px] font-bold text-[var(--ui-ink,#fff)]">{r.employee_id}</span>}
                    </td>
                    <td className="py-2.5 pr-3 font-mono-num">{r.washes_count}</td>
                    <td className="py-2.5 pr-3 font-mono-num">{r.plan_washes_count}</td>
                    <td className="py-2.5 pr-3 font-mono-num">₹{r.cash_amount} <span className="text-xs text-gray-400">({r.cash_count})</span></td>
                    <td className="py-2.5 pr-3 font-mono-num">₹{r.online_amount} <span className="text-xs text-gray-400">({r.online_count})</span></td>
                    <td className={`py-2.5 pr-3 font-mono-num ${r.uncollected_amount > 0 ? "font-bold text-amber-600" : "text-gray-400"}`}>
                      ₹{r.uncollected_amount} <span className="text-xs">({r.uncollected_count})</span>
                    </td>
                    {showExtras && (
                      <td className="py-2.5 pr-3 font-mono-num text-xs text-[var(--color-text-secondary)]">
                        −₹{r.manager_discount_amount || 0} / +₹{r.tip_amount || 0}
                      </td>
                    )}
                    {showBack && (
                      <td className="py-2.5 pr-3 font-mono-num text-xs text-[var(--color-text-secondary)]">
                        {r.paid_back_by_managers != null ? `−${money(r.paid_back_by_managers)}` : "—"}
                      </td>
                    )}
                    {showBack && <td className="py-2.5 font-mono-num text-xs">{r.net_collected != null ? money(r.net_collected) : "—"}</td>}
                  </tr>
                ))}
                {t && (
                  <tr className="border-t border-gray-200 font-bold text-[var(--color-text-primary)]">
                    <td className="py-2.5 pr-3">Total</td>
                    <td className="py-2.5 pr-3 font-mono-num">{t.washes_count}</td>
                    <td className="py-2.5 pr-3 font-mono-num">{t.plan_washes_count}</td>
                    <td className="py-2.5 pr-3 font-mono-num">₹{t.cash_amount}</td>
                    <td className="py-2.5 pr-3 font-mono-num">₹{t.online_amount}</td>
                    <td className={`py-2.5 pr-3 font-mono-num ${t.uncollected_amount > 0 ? "text-amber-600" : ""}`}>₹{t.uncollected_amount}</td>
                    {showExtras && (
                      <td className="py-2.5 pr-3 font-mono-num text-xs">
                        −₹{t.manager_discount_amount || 0} / +₹{t.tip_amount || 0}
                      </td>
                    )}
                    {showBack && <td className="py-2.5 pr-3 font-mono-num text-xs">−{money(t.paid_back_by_managers)}</td>}
                    {showBack && <td className="py-2.5 font-mono-num text-xs">{money(t.net_collected)}</td>}
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}

        {(!!t?.billed_amount || !!t?.wallet_amount) && (
          <p className="mt-3 text-sm text-[var(--color-text-secondary)]" data-testid="collections-billed-wallet">
            {!!t?.billed_amount && (
              <>
                Billed for done washes: <span className="font-mono-num font-bold text-[var(--color-text-primary)]">₹{t.billed_amount}</span>
                {t?.wallet_amount ? " · " : "."}
              </>
            )}
            {!!t?.wallet_amount && (
              <>
                Paid from customer wallets: <span className="font-mono-num font-bold text-[var(--color-text-primary)]">₹{t.wallet_amount}</span> — not cash, not Razorpay.
              </>
            )}
          </p>
        )}

        {!!t?.manual_online_amount && (
          <p className="mt-3 text-sm text-[var(--color-text-secondary)]">
            Online includes <span className="font-mono-num font-bold text-[var(--color-text-primary)]">₹{t.manual_online_amount}</span> UPI recorded by managers on jobs
            they did themselves — that money is not in Razorpay.
          </p>
        )}

        {(!!t?.manager_discount_amount || !!t?.tip_amount) && (
          <p className="mt-3 text-sm text-[var(--color-text-secondary)]" data-testid="collections-discount-tip">
            {!!t?.manager_discount_amount && (
              <>
                Manager discounts: <span className="font-mono-num font-bold text-[var(--color-text-primary)]">₹{t.manager_discount_amount}</span> taken off jobs managers did
                {t?.tip_amount ? " · " : "."}
              </>
            )}
            {!!t?.tip_amount && (
              <>
                Tips: <span className="font-mono-num font-bold text-[var(--color-text-primary)]">₹{t.tip_amount}</span>
                {t.tips_cash != null || t.tips_online != null ? (
                  <>
                    {" "}
                    (<span className="font-mono-num">{money(t.tips_cash)}</span> cash · <span className="font-mono-num">{money(t.tips_online)}</span> online)
                  </>
                ) : null}{" "}
                — already inside the amounts above.
              </>
            )}
          </p>
        )}

        {!!back?.amount && (
          <p className="mt-3 text-sm text-[var(--color-text-secondary)]" data-testid="collections-paid-back">
            Paid back by managers: <span className="font-mono-num font-bold text-[var(--color-text-primary)]">{money(back.amount)}</span> ({back.count}) —{" "}
            <span className="font-mono-num">{money(back.wallet_amount)}</span> from wallets + <span className="font-mono-num">{money(back.goodwill_amount)}</span> paid by managers ·{" "}
            <span className="font-mono-num">{money(back.cash_amount)}</span> cash · <span className="font-mono-num">{money(back.online_amount)}</span> online.
          </p>
        )}
        {t?.net_collected != null && !!t?.paid_back_by_managers && (
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]" data-testid="collections-net">
            Net collected: <span className="font-mono-num font-bold text-[var(--color-text-primary)]">{money(t.net_collected)}</span> — cash + online, less paid back.
          </p>
        )}

        {data?.subscriptions && (
          <p className="mt-3 text-sm text-[var(--color-text-secondary)]">
            Subscription revenue: <span className="font-mono-num font-bold text-[var(--color-text-primary)]">₹{data.subscriptions.online_amount}</span> online
            {!!data.subscriptions.cash_amount && (
              <>
                {" "}
                + <span className="font-mono-num font-bold text-[var(--color-text-primary)]">₹{data.subscriptions.cash_amount}</span> cash
                {data.subscriptions.cash_count ? ` (${data.subscriptions.cash_count} sold by managers)` : ""}
              </>
            )}{" "}
            across {data.subscriptions.count} purchase{data.subscriptions.count === 1 ? "" : "s"}.
          </p>
        )}

        {data?.society && (data.society.count > 0 || data.society.online_amount > 0 || data.society.cash_amount > 0) && (
          <p className="mt-3 text-sm text-[var(--color-text-secondary)]">
            Society plans: <span className="font-mono-num font-bold text-[var(--color-text-primary)]">₹{data.society.online_amount}</span> online
            {!!data.society.cash_amount && (
              <>
                {" "}
                + <span className="font-mono-num font-bold text-[var(--color-text-primary)]">₹{data.society.cash_amount}</span> cash
              </>
            )}{" "}
            across {data.society.count} payment{data.society.count === 1 ? "" : "s"}.
          </p>
        )}

        {data?.custom_plans && (data.custom_plans.count > 0 || data.custom_plans.online_amount > 0 || data.custom_plans.cash_amount > 0) && (
          <p className="mt-3 text-sm text-[var(--color-text-secondary)]" data-testid="collections-custom-plans">
            Custom plans: <span className="font-mono-num font-bold text-[var(--color-text-primary)]">₹{data.custom_plans.online_amount}</span> online
            {!!data.custom_plans.cash_amount && (
              <>
                {" "}
                + <span className="font-mono-num font-bold text-[var(--color-text-primary)]">₹{data.custom_plans.cash_amount}</span> cash
              </>
            )}{" "}
            across {data.custom_plans.count} plan{data.custom_plans.count === 1 ? "" : "s"}.
          </p>
        )}

        {data?.wallet && (data.wallet.credits_held > 0 || data.wallet.dues_outstanding > 0 || data.wallet.payouts_count > 0) && (
          <p className="mt-3 text-sm text-[var(--color-text-secondary)]" data-testid="collections-wallet">
            Customer wallets now:{" "}
            <span className="font-mono-num font-bold text-[var(--color-success)]">₹{data.wallet.credits_held}</span> credit held ({data.wallet.credit_customers}) ·{" "}
            <span className="font-mono-num font-bold text-[var(--color-error)]">₹{data.wallet.dues_outstanding}</span> dues outstanding ({data.wallet.due_customers}) ·{" "}
            <span className="font-mono-num font-bold text-[var(--color-text-primary)]">₹{data.wallet.payouts_amount}</span> paid back ({data.wallet.payouts_count}).
          </p>
        )}

        {data?.refunds && (data.refunds.due_count > 0 || data.refunds.refunded_count > 0) && (
          <p className="mt-3 text-sm text-[var(--color-text-secondary)]" data-testid="collections-refunds">
            Refunds:{" "}
            {data.refunds.due_count > 0 && (
              <>
                <span className="font-mono-num font-bold text-amber-700">₹{data.refunds.due_amount}</span> owed back ({data.refunds.due_count})
                {data.refunds.refunded_count > 0 ? " · " : "."}
              </>
            )}
            {data.refunds.refunded_count > 0 && (
              <>
                <span className="font-mono-num font-bold text-[var(--color-text-primary)]">₹{data.refunds.refunded_amount}</span> returned ({data.refunds.refunded_count}).
              </>
            )}{" "}
            Not counted as revenue.
          </p>
        )}

        {!!data?.attention?.length && (
          <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50/50 p-3">
            <p className="mb-2 flex items-center gap-1.5 text-sm font-bold text-amber-800">
              <AlertTriangle className="h-4 w-4" /> Payments Needing Attention ({data.attention.length})
            </p>
            <div className="space-y-1.5">
              {data.attention.map((a, i) => (
                <AttentionRow key={a.id || i} item={a} queryKey={queryKey} />
              ))}
            </div>
            <p className="mt-2 text-[11px] text-amber-700">Money was received but couldn't be applied automatically — refund or fix it in Razorpay (search the payment id), then mark it resolved here.</p>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

/** One parked payment, with the Razorpay payment id to look up and a
 *  "resolved" action that records what was done (refund, activation…). */
function AttentionRow({ item, queryKey }: { item: NonNullable<BaseReport["attention"]>[number]; queryKey: string }) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const [outcome, setOutcome] = useState<"refunded" | "activated" | "other">(item.refund_status === "due" ? "refunded" : "other");
  const [refund, setRefund] = useState("");
  const [open, setOpen] = useState(false);
  const refundNum = refund.trim() === "" ? undefined : Number(refund);
  const refundValid = refundNum === undefined || (Number.isFinite(refundNum) && refundNum > 0 && refundNum <= item.amount);
  const resolve = useMutation({
    mutationFn: () => paymentApi.resolveAttention(item.id!, note.trim(), outcome, outcome === "refunded" ? refundNum : undefined),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [queryKey] });
      setOpen(false);
    },
  });
  return (
    <div className="text-xs text-amber-900">
      <p>
        <span className="font-mono-num font-bold">₹{item.amount}</span>
        {item.booking_number
          ? ` · ${item.booking_number}`
          : item.purpose === "subscription"
            ? ` · ${[toTitle(item.plan_name) || "Plan", carAndService(item.vehicle_type_name, item.service_name)].filter(Boolean).join(" · ")}`
            : ""}{" "}
        — {item.refund_status === "due" ? "Refund due (paid, then cancelled)" : item.reason}
        {item.flagged_at ? ` (${formatDateTime(item.flagged_at)})` : ""}
        {item.payment_id ? <span className="font-mono-num"> · {item.payment_id}</span> : null}
        {item.id && !open && (
          <button type="button" className="ml-2 font-semibold underline" onClick={() => setOpen(true)}>
            Mark Resolved
          </button>
        )}
      </p>
      <Modal open={open} onClose={() => setOpen(false)} title="Resolve Payment" maxWidth="max-w-sm">
        <div className="space-y-4">
          <p className="text-sm text-[var(--color-text-secondary)]">
            <span className="font-mono-num font-semibold text-[#0E1A33]">₹{item.amount}</span>
            {item.booking_number ? ` · ${item.booking_number}` : ""}
            {item.payment_id ? <span className="font-mono-num"> · {item.payment_id}</span> : null}
          </p>
          <fieldset>
            <legend className="mb-2 text-sm font-semibold text-[#0E1A33]">What Was Done?</legend>
            <div className="grid gap-2" role="radiogroup">
              {[
                { value: "refunded" as const, label: "Refunded In Razorpay", sub: "The booking then reads Refunded" },
                { value: "activated" as const, label: "Applied / Activated", sub: "The booking or plan was fixed by hand" },
                { value: "other" as const, label: "Something Else", sub: "Explain in the note" },
              ].map((o) => (
                <button
                  key={o.value}
                  type="button"
                  role="radio"
                  aria-checked={outcome === o.value}
                  onClick={() => setOutcome(o.value)}
                  className={`rounded-xl border px-3 py-2 text-left ${outcome === o.value ? "border-black bg-[var(--color-primary-light)] ring-1 ring-black" : "border-gray-200 hover:border-gray-300"}`}
                >
                  <span className="block text-sm font-semibold text-[#0E1A33]">{o.label}</span>
                  <span className="block text-xs text-[#5F6878]">{o.sub}</span>
                </button>
              ))}
            </div>
          </fieldset>
          {outcome === "refunded" && (
            <Input
              label="Refund Amount (₹)"
              inputMode="decimal"
              value={refund}
              onChange={(e) => setRefund(e.target.value.replace(/[^\d.]/g, "").slice(0, 9))}
              placeholder={String(item.amount)}
              error={!refundValid ? `Between ₹1 and ₹${item.amount}.` : undefined}
              hint={`Leave empty for the full ₹${item.amount}; type less for a partial refund.`}
            />
          )}
          <Input label="Note" value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Refunded in Razorpay, ref rfnd_…" />
          {resolve.isError && <p className="text-sm text-[var(--color-error)]">{getErrorMessage(resolve.error)}</p>}
          <div className="flex gap-2">
            <Button variant="outline" className="flex-1" onClick={() => setOpen(false)}>
              Back
            </Button>
            <Button className="flex-1" isLoading={resolve.isPending} disabled={note.trim().length < 3 || !refundValid} onClick={() => resolve.mutate()}>
              Save
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
