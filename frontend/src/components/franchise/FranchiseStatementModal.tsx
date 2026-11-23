import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, HandCoins } from "lucide-react";
import { franchiseApi, type FranchiseCenterRow, type FranchiseLedgerRow, type FranchiseMethod, type FranchiseSettlementKind } from "../../api/franchise";
import { Badge, Button, ErrorState, Input, Modal, Select, StatCard } from "../ui";
import { PageLoader } from "../ui/Spinner";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";
import { format as formatDate, formatDateTime, todayIST } from "../../lib/date";
import { toTitle } from "../../lib/titleCase";
import { inr, rupeesToPaise } from "./money";

const KIND_LABEL: Record<string, string> = {
  share: "Share Accrued",
  share_adjust: "Share Adjustment",
  settlement_pay: "Paid To Partner",
  settlement_receive: "Received From Partner",
  franchise_fee: "Franchise Fee",
  kit_sale: "Kit Sale",
};
/** Rows an admin recorded by hand — the only ones that can be voided. */
// (the server marks them with a settlement_kind; share rows have none)

const SETTLE_OPTIONS: { value: FranchiseSettlementKind; label: string; hint: string }[] = [
  { value: "pay_partner", label: "Pay Partner", hint: "Blussit pays the partner their share (a debit in Finance)." },
  { value: "receive_from_partner", label: "Receive From Partner", hint: "The partner hands over cash they hold (a credit in Finance)." },
  { value: "franchise_fee", label: "Franchise Fee", hint: "The partner pays the franchise fee (a credit in Finance)." },
  { value: "kit_sale", label: "Kit Sale", hint: "The partner buys kits from Blussit (a credit in Finance)." },
];
const METHODS: { value: FranchiseMethod; label: string }[] = [
  { value: "cash", label: "Cash" },
  { value: "upi", label: "UPI" },
  { value: "bank", label: "Bank Transfer" },
  { value: "other", label: "Other" },
];

/** The last 18 months, newest first, as "YYYY-MM", then All Time (""). */
function monthOptions(): { value: string; label: string }[] {
  const [y, m] = todayIST().split("-").map(Number);
  const list = Array.from({ length: 18 }, (_, i) => {
    const d = new Date(Date.UTC(y, m - 1 - i, 1, 12));
    return {
      value: `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`,
      label: d.toLocaleDateString("en-IN", { month: "long", year: "numeric", timeZone: "UTC" }),
    };
  });
  return [...list, { value: "", label: "All Time" }];
}

const PAGE = 25;

export function FranchiseStatementModal({ center, onClose }: { center: FranchiseCenterRow | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const months = useMemo(monthOptions, []);
  const [month, setMonth] = useState(months[0].value);
  const [shown, setShown] = useState(PAGE);
  const id = center?.id;

  useEffect(() => {
    if (id) {
      setMonth(months[0].value);
      setShown(PAGE);
    }
  }, [id, months]);

  const statement = useQuery({
    queryKey: ["franchise-statement", id, month],
    queryFn: () => franchiseApi.statement(id!, month),
    enabled: !!id,
  });
  const st = statement.data;
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["franchise-statement"] });
    void queryClient.invalidateQueries({ queryKey: ["franchise-centers"] });
    void queryClient.invalidateQueries({ queryKey: ["finance-summary"] });
    void queryClient.invalidateQueries({ queryKey: ["finance-entries"] });
  };

  // ----- record settlement -----
  const [settling, setSettling] = useState(false);
  const [kind, setKind] = useState<FranchiseSettlementKind>("pay_partner");
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState<FranchiseMethod>("upi");
  const [date, setDate] = useState(todayIST());
  const [note, setNote] = useState("");
  const [formError, setFormError] = useState("");
  // One key per opening of the dialog: a double click or a retry after a lost
  // reply is the same request to the server, never a second payment.
  const idemKey = useRef("");
  const openSettle = (preset?: FranchiseSettlementKind) => {
    idemKey.current = crypto.randomUUID();
    const owed = st?.all_time.net_payable_paise ?? center?.net_payable_paise ?? 0;
    const k = preset ?? (owed < 0 ? "receive_from_partner" : "pay_partner");
    setKind(k);
    const pending = st?.fee.fee_pending_paise ?? center?.fee_pending_paise ?? 0;
    const suggest = k === "franchise_fee" ? pending : k === "pay_partner" || k === "receive_from_partner" ? Math.abs(owed) : 0;
    setAmount(suggest > 0 ? String(Math.round(suggest / 100)) : "");
    setMethod("upi");
    setDate(todayIST());
    setNote("");
    setFormError("");
    setSettling(true);
  };
  const settle = useMutation({
    mutationFn: (paise: number) =>
      franchiseApi.settle(id!, { kind, amount_paise: paise, method, paid_on: date, note: note.trim() || undefined, idempotency_key: idemKey.current }),
    onSuccess: (res) => {
      pushToast({ tone: "success", title: res.message || "Settlement Recorded" });
      setSettling(false);
      refresh();
    },
    onError: (err) => setFormError(getErrorMessage(err)),
  });
  const submitSettle = () => {
    setFormError("");
    const paise = rupeesToPaise(amount);
    if (paise == null) return setFormError("Enter an amount in rupees, like 15000.");
    if (!date) return setFormError("Pick the date of this payment.");
    if (kind === "franchise_fee" && st && paise > st.fee.fee_pending_paise) {
      return setFormError(st.fee.fee_pending_paise > 0 ? `That is more than the fee still pending (${inr(st.fee.fee_pending_paise)}).` : "This center has no franchise fee pending.");
    }
    settle.mutate(paise);
  };

  // ----- void -----
  const [voiding, setVoiding] = useState<FranchiseLedgerRow | null>(null);
  const [reason, setReason] = useState("");
  const [voidError, setVoidError] = useState("");
  const voidIt = useMutation({
    mutationFn: () => franchiseApi.voidSettlement(id!, voiding!.id, reason.trim()),
    onSuccess: (res) => {
      pushToast({ tone: "success", title: res.message || "Entry Voided" });
      setVoiding(null);
      setReason("");
      refresh();
    },
    onError: (err) => setVoidError(getErrorMessage(err)),
  });

  const all = st?.all_time;
  const view = st?.month_view ?? all;
  const net = all?.net_payable_paise ?? 0;
  const ledger = st?.ledger ?? [];

  return (
    <>
      <Modal open={!!center} onClose={onClose} title={center ? `${center.name} — Statement` : "Statement"} maxWidth="max-w-3xl">
        {center && (
          <div className="space-y-5">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div className="min-w-0 text-sm text-[var(--color-text-secondary)]">
                <p className="font-medium text-[var(--color-text-primary)]">{center.partner?.name ? toTitle(center.partner.name) : "Partner"}</p>
                <p>
                  {[center.partner?.phone, center.partner?.email].filter(Boolean).join(" · ") || "No contact saved"} · Partner Share {center.revenue_share_percent ?? 0}%
                </p>
              </div>
              <div className="flex flex-wrap items-end gap-2">
                <div className="w-44">
                  <Select compact aria-label="Month" value={month} onChange={(e) => { setMonth(e.target.value); setShown(PAGE); }}>
                    {months.map((m) => (
                      <option key={m.value} value={m.value}>{m.label}</option>
                    ))}
                  </Select>
                </div>
                <Button size="sm" onClick={() => openSettle()}>
                  <HandCoins className="h-4 w-4" /> Record Settlement
                </Button>
              </div>
            </div>

            {statement.isLoading ? (
              <PageLoader />
            ) : statement.isError ? (
              <ErrorState message={getErrorMessage(statement.error)} onRetry={() => void statement.refetch()} busy={statement.isFetching} />
            ) : st ? (
              <>
                {view && (
                  <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
                    <StatCard label="Bookings Completed" value={view.bookings_completed.toLocaleString("en-IN")} />
                    <StatCard label="Booking Value" value={inr(view.base_paise)} hint="After discounts, before tips" />
                    <StatCard label="Share Accrued" value={inr(view.share_accrued_paise)} hint={`Partner's ${center.revenue_share_percent ?? 0}%`} />
                    <StatCard label="Cash Held By Partner" value={inr(view.cash_held_paise)} hint="Collected by their captains" />
                    <StatCard label="Online Collected" value={inr(view.online_collected_paise)} hint="Received by Blussit" />
                    <StatCard
                      label={net < 0 ? "Net Receivable From Partner" : "Net Payable To Partner"}
                      value={inr(Math.abs(net))}
                      tone={net === 0 ? "default" : net < 0 ? "warning" : "error"}
                      hint={
                        all && (all.settled_paid_paise || all.settled_received_paise)
                          ? `All time · ${inr(all.settled_paid_paise)} paid, ${inr(all.settled_received_paise)} received`
                          : "All time: share minus cash held, after settlements"
                      }
                    />
                  </div>
                )}

                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div className="rounded-xl border border-[var(--color-card-border)] p-3 text-sm">
                    <p className="text-xs font-medium text-[var(--color-text-secondary)]">Franchise Fee</p>
                    <p className="mt-1 font-semibold text-[var(--color-text-primary)]">{inr(st.fee.franchise_fee_paise)}</p>
                    <p className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-secondary)]">
                      <Badge tone={st.fee.fee_status === "received" ? "success" : st.fee.fee_status === "none" ? "neutral" : "warning"}>
                        {st.fee.fee_status === "none" ? "No Fee" : st.fee.fee_status === "received" ? "Received" : st.fee.fee_status === "partial" ? `${inr(st.fee.fee_received_paise)} Received` : "Pending"}
                      </Badge>
                      {st.fee.fee_pending_paise > 0 && (
                        <button type="button" className="font-medium text-[var(--color-primary)] hover:underline" onClick={() => openSettle("franchise_fee")}>Record Fee</button>
                      )}
                    </p>
                  </div>
                  <div className="rounded-xl border border-[var(--color-card-border)] p-3 text-sm">
                    <p className="text-xs font-medium text-[var(--color-text-secondary)]">Kits</p>
                    <p className="mt-1 font-semibold text-[var(--color-text-primary)]">{st.center.kits_purchased} Purchased · {st.center.kits_assigned} With Captains</p>
                    <p className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-secondary)]">
                      <span>{st.kit_sales_paise ? `${inr(st.kit_sales_paise)} Received` : "No Kit Payment Recorded"}</span>
                      <button type="button" className="font-medium text-[var(--color-primary)] hover:underline" onClick={() => openSettle("kit_sale")}>Record Kit Sale</button>
                    </p>
                    {st.center.kits_warning && <p className="mt-1 text-xs text-amber-700">{st.center.kits_warning}</p>}
                  </div>
                </div>

                <div>
                  <h4 className="mb-2 text-sm font-semibold text-[var(--color-text-primary)]">Ledger</h4>
                  {ledger.length === 0 ? (
                    <p className="rounded-xl border border-dashed border-gray-300 p-6 text-center text-sm text-[var(--color-text-secondary)]">Nothing recorded for this month yet.</p>
                  ) : (
                    <>
                      <ul className="divide-y divide-[var(--color-card-border)] rounded-xl border border-[var(--color-card-border)]">
                        {ledger.slice(0, shown).map((r) => (
                          <li key={r.id} className="flex items-start justify-between gap-3 px-3 py-2.5 text-sm">
                            <div className="min-w-0">
                              <p className={`font-medium ${r.voided ? "text-gray-400 line-through" : "text-[var(--color-text-primary)]"}`}>
                                {KIND_LABEL[r.kind] ?? r.kind}
                                {r.method && <span className="ml-2 text-xs font-normal text-[var(--color-text-secondary)]">{METHODS.find((m) => m.value === r.method)?.label ?? r.method}</span>}
                                {r.voided && <Badge className="ml-2" tone="neutral">Voided</Badge>}
                              </p>
                              <p className="text-xs text-[var(--color-text-secondary)]">
                                {r.paid_on ? formatDate(r.paid_on) : r.created_at ? formatDateTime(r.created_at) : ""}
                                {r.actor?.name && ` · ${r.actor.name}`}
                                {(r.booking_number || r.booking_id) && ` · Booking ${r.booking_number ?? `…${r.booking_id!.slice(-6)}`}`}
                              </p>
                              {r.note && <p className="break-words text-xs text-[var(--color-text-secondary)]">{r.note}</p>}
                              {r.voided && r.void_reason && <p className="text-xs text-[var(--color-error)]">Voided: {r.void_reason}</p>}
                            </div>
                            <div className="flex shrink-0 items-center gap-2">
                              <span className={`font-mono-num font-semibold ${r.voided ? "text-gray-400 line-through" : "text-[var(--color-text-primary)]"}`}>
                                {r.amount_paise < 0 ? "-" : ""}{inr(Math.abs(r.amount_paise))}
                              </span>
                              {r.settlement_kind && !r.voided && (
                                <Button size="sm" variant="ghost" aria-label={`Void ${KIND_LABEL[r.kind] ?? r.kind}`} title="Void" onClick={() => { setVoiding(r); setReason(""); setVoidError(""); }}>
                                  <Ban className="h-3.5 w-3.5" />
                                </Button>
                              )}
                            </div>
                          </li>
                        ))}
                      </ul>
                      {ledger.length > shown && (
                        <div className="mt-3 text-center">
                          <Button size="sm" variant="outline" onClick={() => setShown((n) => n + PAGE)}>Show More ({ledger.length - shown} Left)</Button>
                        </div>
                      )}
                    </>
                  )}
                </div>
              </>
            ) : null}
          </div>
        )}
      </Modal>

      <Modal open={settling} onClose={() => setSettling(false)} title="Record Settlement" maxWidth="max-w-md">
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); submitSettle(); }}>
          <Select label="Type" value={kind} onChange={(e) => setKind(e.target.value as FranchiseSettlementKind)}>
            {SETTLE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </Select>
          <p className="-mt-2 text-xs text-[var(--color-text-secondary)]">{SETTLE_OPTIONS.find((o) => o.value === kind)?.hint}</p>
          <div className="grid grid-cols-2 gap-3">
            <Input label="Amount (₹)" inputMode="decimal" value={amount} placeholder="15000" onChange={(e) => setAmount(e.target.value.replace(/[^\d.]/g, ""))} required />
            <Input label="Date" type="date" value={date} max={todayIST()} onChange={(e) => setDate(e.target.value)} required />
          </div>
          <Select label="Method" value={method} onChange={(e) => setMethod(e.target.value as FranchiseMethod)}>
            {METHODS.map((m) => (
              <option key={m.value} value={m.value}>{m.label}</option>
            ))}
          </Select>
          <Input label="Note (Optional)" value={note} maxLength={200} placeholder="Reference or UTR" onChange={(e) => setNote(e.target.value)} />
          {formError && <p role="alert" className="text-sm text-[var(--color-error)]">{formError}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => setSettling(false)}>Cancel</Button>
            <Button type="submit" isLoading={settle.isPending}>Record</Button>
          </div>
        </form>
      </Modal>

      <Modal open={!!voiding} onClose={() => setVoiding(null)} title="Void This Entry?" maxWidth="max-w-md">
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            setVoidError("");
            if (reason.trim().length < 3) return setVoidError("Write a short reason (at least 3 letters).");
            voidIt.mutate();
          }}
        >
          <p className="text-sm text-[var(--color-text-secondary)]">
            {voiding ? `${KIND_LABEL[voiding.kind] ?? voiding.kind} · ${inr(Math.abs(voiding.amount_paise))}` : ""}. It stays in the records, stops counting in the statement and its Finance entry is voided too.
          </p>
          <Input label="Reason" value={reason} maxLength={200} placeholder="e.g. Entered twice" onChange={(e) => setReason(e.target.value)} required />
          {voidError && <p role="alert" className="text-sm text-[var(--color-error)]">{voidError}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => setVoiding(null)}>Keep It</Button>
            <Button type="submit" variant="danger" isLoading={voidIt.isPending}>Void Entry</Button>
          </div>
        </form>
      </Modal>
    </>
  );
}
