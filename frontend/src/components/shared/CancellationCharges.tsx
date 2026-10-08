import { useEffect, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { History, Minus, X } from "lucide-react";
import { CHARGE_STATUS_LABELS, chargeApi, TIER_LABELS, type ChargeStatus, type CustomerCharge } from "../../api/charges";
import { getErrorMessage } from "../../lib/api-client";
import { asUtcInstant, formatDateTime } from "../../lib/date";
import { toTitle } from "../../lib/titleCase";
import { useToast } from "../../context/ToastContext";
import { Badge, Button, DataTable, ErrorState, Input, Modal, Spinner, type Column } from "../ui";
import { Pager } from "./ListControls";

/**
 * Late-cancellation charges for staff (founder rule 2026-10-07). The charge
 * is settled through the customer's WALLET ("Added To Wallet As Due" — a
 * debit there, or netted against what they had paid). A manager (own
 * center) or admin can REDUCE it or WAIVE it (0) — never raise it — with a
 * note: the reduction is credited back to the wallet, or lowers the unpaid
 * booking now carrying that due. Every step is on the charge's history.
 * Legacy charges added to a booking can't change once that booking is paid.
 */

const STATUS_TONE: Record<ChargeStatus, "warning" | "info" | "neutral"> = { open: "warning", applied: "info", waived: "neutral", settled: "warning" };

/** Reduce / waive is possible while nothing has been paid against it. */
export const canAdjustCharge = (c: CustomerCharge) =>
  c.status === "settled" || c.status === "open" || (c.status === "applied" && !c.applied_booking_paid);

/** What waiving gave back: the amount it was before the waiver. */
function waivedAmount(c: CustomerCharge): number {
  const last = [...(c.history || [])].reverse().find((h) => h.action === "waived");
  return Math.round(Number(last?.from ?? c.original_amount ?? 0));
}

/** One line under the status badge, in the words the founder uses. */
export function chargeOutcome(c: CustomerCharge): string {
  if (c.status === "waived") {
    if (c.applied_to_booking_number) return `Waived — Removed From ${c.applied_to_booking_number}`;
    const back = waivedAmount(c);
    return back > 0 ? `Waived — ₹${back} Credited` : "Waived";
  }
  if (c.status === "settled") {
    const reduced = Math.round(c.original_amount) - Math.round(c.amount);
    return reduced > 0 ? `Reduced — ₹${reduced} Credited` : "";
  }
  return "";
}

function invalidateCharges(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({
    predicate: (q) => typeof q.queryKey[0] === "string" && /^(charges|customer-360|center-bookings|admin-center-bookings|booking)/.test(q.queryKey[0]),
  });
}

const HISTORY_LABELS: Record<string, string> = {
  created: "Charged",
  applied: "Added To Booking",
  released: "Charged To Wallet",
  moved_to_wallet: "Moved To Wallet",
  reduced: "Reduced",
  waived: "Waived",
};

/** Amount box + note; "waive" sends 0. */
export function ChargeAdjustDialog({ charge, mode, onClose }: { charge: CustomerCharge | null; mode: "reduce" | "waive"; onClose: () => void }) {
  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!charge) return;
    setAmount(mode === "waive" ? "0" : "");
    setNote("");
    setError("");
  }, [charge, mode]);

  const current = Math.round(charge?.amount ?? 0);
  const value = mode === "waive" ? 0 : amount.trim() === "" ? NaN : Math.round(Number(amount));
  const valid = Number.isFinite(value) && value >= 0 && value < current;
  const save = useMutation({
    mutationFn: () => chargeApi.adjust(charge!.id, value, note.trim() || undefined),
    onSuccess: () => {
      invalidateCharges(queryClient);
      queryClient.invalidateQueries({ queryKey: ["customer-wallet"] });
      const back = current - value;
      pushToast({
        tone: "success",
        title: value === 0 ? `Charge Waived — ₹${back} Credited` : `Charge Reduced To ₹${value}`,
        message: charge?.status === "settled" && value > 0 ? `₹${back} credited to the wallet.` : undefined,
      });
      onClose();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  return (
    <Modal open={!!charge} onClose={onClose} title={mode === "waive" ? "Waive Charge" : "Reduce Charge"} maxWidth="max-w-sm">
      {charge && (
        <div className="space-y-4">
          <p className="text-sm text-[var(--color-text-secondary)]">
            <span className="font-semibold text-[#0E1A33]">{charge.customer_name || "Customer"}</span> · ₹{current} for cancelling{" "}
            <span className="font-mono-num">{charge.source_booking_number || "a booking"}</span> late.
            {charge.status === "applied" && charge.applied_to_booking_number ? (
              <> It&apos;s on <span className="font-mono-num">{charge.applied_to_booking_number}</span> — that booking&apos;s total changes too.</>
            ) : charge.status === "settled" ? (
              <> The difference is credited to their wallet — or comes off the unpaid booking now carrying it.</>
            ) : null}
          </p>
          {mode === "reduce" ? (
            <Input
              label="New Amount (₹)"
              inputMode="numeric"
              autoFocus
              value={amount}
              onChange={(e) => setAmount(e.target.value.replace(/\D/g, "").slice(0, 4))}
              error={amount.trim() !== "" && !valid ? `Less than ₹${current} — use Waive for ₹0.` : undefined}
              hint={`Now ₹${current}. A charge can only go down.`}
            />
          ) : (
            <p className="rounded-xl bg-[var(--color-primary-light)] px-3 py-2.5 text-sm text-[#0E1A33]">
              The customer won&apos;t pay anything for this cancellation{charge.status === "settled" ? ` — ₹${current} goes back to their wallet` : ""}.
            </p>
          )}
          <Input label="Note (Optional)" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Customer called — genuine emergency" />
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <div className="flex gap-2">
            <Button variant="outline" className="flex-1" onClick={onClose}>
              Back
            </Button>
            <Button className="flex-1" isLoading={save.isPending} disabled={!valid} onClick={() => save.mutate()}>
              {mode === "waive" ? "Waive Charge" : "Save"}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

export function ChargeHistoryDialog({ charge, onClose }: { charge: CustomerCharge | null; onClose: () => void }) {
  return (
    <Modal open={!!charge} onClose={onClose} title="Charge History" maxWidth="max-w-md">
      {charge && (
        <div className="space-y-4">
          <div className="text-sm text-[var(--color-text-secondary)]">
            <p>
              <span className="font-semibold text-[#0E1A33]">{charge.customer_name || "Customer"}</span>
              {charge.customer_phone ? ` · ${charge.customer_phone}` : ""}
            </p>
            <p className="mt-0.5">
              Cancelled <span className="font-mono-num">{charge.source_booking_number || "—"}</span> · {TIER_LABELS[charge.tier] || charge.tier}
            </p>
          </div>
          <ol className="space-y-3 border-l-2 border-[#E4E9F1] pl-4">
            {[...(charge.history || [])].reverse().map((h, i) => (
              <li key={i} className="relative text-sm">
                <span className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full bg-[var(--color-primary)]" />
                <p className="font-semibold text-[#0E1A33]">
                  {HISTORY_LABELS[h.action] || toTitle(h.action)}
                  {h.from != null && h.to != null && h.from !== h.to ? (
                    <span className="font-mono-num font-normal text-[#5F6878]">
                      {" "}
                      · ₹{Math.round(h.from)} → ₹{Math.round(h.to)}
                    </span>
                  ) : h.to != null ? (
                    <span className="font-mono-num font-normal text-[#5F6878]"> · ₹{Math.round(h.to)}</span>
                  ) : null}
                </p>
                <p className="text-xs text-[#5F6878]">
                  {[h.at ? formatDateTime(asUtcInstant(h.at)) : "", h.by_name ? `${h.by_name}${h.role && toTitle(h.role) !== h.by_name ? ` (${toTitle(h.role)})` : ""}` : h.role === "system" ? "System" : ""].filter(Boolean).join(" · ")}
                </p>
                {h.note && <p className="mt-0.5 text-xs text-[#0E1A33]">&ldquo;{h.note}&rdquo;</p>}
              </li>
            ))}
          </ol>
          <Button variant="outline" className="w-full" onClick={onClose}>
            Close
          </Button>
        </div>
      )}
    </Modal>
  );
}

function AmountCell({ c }: { c: CustomerCharge }) {
  const reduced = Math.round(c.original_amount) > Math.round(c.amount);
  return (
    <span className="font-mono-num whitespace-nowrap">
      <span className="font-semibold">₹{Math.round(c.amount)}</span>
      {reduced && <span className="ml-1 text-xs text-gray-400 line-through">₹{Math.round(c.original_amount)}</span>}
    </span>
  );
}

function StatusCell({ c }: { c: CustomerCharge }) {
  const outcome = chargeOutcome(c);
  return (
    <div className="space-y-0.5">
      <Badge tone={STATUS_TONE[c.status] || "neutral"}>{CHARGE_STATUS_LABELS[c.status] || toTitle(c.status)}</Badge>
      {outcome && <p className="text-xs text-[var(--color-text-secondary)]">{outcome}</p>}
      {c.status === "applied" && c.applied_to_booking_number && (
        <p className="text-xs text-[var(--color-text-secondary)]">
          On <span className="font-mono-num">{c.applied_to_booking_number}</span>
          {c.applied_booking_paid ? " · Paid" : ""}
        </p>
      )}
    </div>
  );
}

function ChargeActions({ c, onAdjust, onHistory }: { c: CustomerCharge; onAdjust: (c: CustomerCharge, mode: "reduce" | "waive") => void; onHistory: (c: CustomerCharge) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
      {canAdjustCharge(c) && (
        <>
          <Button size="sm" variant="outline" className="min-h-11 sm:min-h-0" disabled={Math.round(c.amount) <= 1} onClick={() => onAdjust(c, "reduce")}>
            <Minus className="h-3.5 w-3.5" /> Reduce
          </Button>
          <Button size="sm" variant="outline" className="min-h-11 sm:min-h-0" onClick={() => onAdjust(c, "waive")}>
            <X className="h-3.5 w-3.5" /> Waive
          </Button>
        </>
      )}
      <Button size="sm" variant="ghost" className="min-h-11 sm:min-h-0" onClick={() => onHistory(c)}>
        <History className="h-3.5 w-3.5" /> History
      </Button>
    </div>
  );
}

const STATUS_FILTERS: { label: string; value: "" | ChargeStatus }[] = [
  { label: "Added To Wallet As Due", value: "settled" },
  { label: "Waived", value: "waived" },
  { label: "Added To Booking (Old)", value: "applied" },
  { label: "All", value: "" },
];

/** Every charge a manager (own center) or admin (all) can see. */
export function ChargesList({ centerNames }: { /** Admin: center id → name, adds a Center column. */ centerNames?: Record<string, string> }) {
  const [status, setStatus] = useState<"" | ChargeStatus>("settled");
  const [page, setPage] = useState(1);
  const [adjusting, setAdjusting] = useState<{ charge: CustomerCharge; mode: "reduce" | "waive" } | null>(null);
  const [history, setHistory] = useState<CustomerCharge | null>(null);
  useEffect(() => setPage(1), [status]);
  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ["charges", "list", status, page],
    queryFn: () => chargeApi.list({ status: status || undefined, page, page_size: 25 }),
    placeholderData: keepPreviousData,
  });

  const columns: Column<CustomerCharge>[] = [
    {
      header: "Customer",
      accessor: (c) => (
        <div className="min-w-0">
          <p className="font-medium">{c.customer_name || "—"}</p>
          {c.customer_phone && <p className="text-xs text-[var(--color-text-secondary)]">{c.customer_phone}</p>}
        </div>
      ),
    },
    {
      header: "Cancelled Booking",
      accessor: (c) => (
        <div>
          <p className="font-mono-num text-xs font-semibold">{c.source_booking_number || "—"}</p>
          <p className="text-xs text-[var(--color-text-secondary)]">{TIER_LABELS[c.tier] || c.tier}</p>
        </div>
      ),
    },
    { header: "Amount", accessor: (c) => <AmountCell c={c} /> },
    { header: "Status", accessor: (c) => <StatusCell c={c} /> },
    {
      header: "Charged",
      accessor: (c) => (
        <div className="text-xs text-[var(--color-text-secondary)]">
          <p className="whitespace-nowrap">{c.created_at ? formatDateTime(c.created_at) : "—"}</p>
          {c.created_by_name && <p>By {c.created_by_name}</p>}
        </div>
      ),
    },
    ...(centerNames
      ? [{ header: "Center", accessor: (c: CustomerCharge) => <span className="text-xs">{(c.service_center_id && centerNames[c.service_center_id]) || "—"}</span> }]
      : []),
    { header: "", accessor: (c) => <ChargeActions c={c} onAdjust={(charge, mode) => setAdjusting({ charge, mode })} onHistory={setHistory} /> },
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.label}
            type="button"
            onClick={() => setStatus(f.value)}
            aria-pressed={status === f.value}
            className={`min-h-11 rounded-full px-3.5 py-1.5 text-sm font-medium transition-colors sm:min-h-0 ${
              status === f.value ? "bg-[var(--color-primary)] text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>
      <DataTable<CustomerCharge>
        columns={columns}
        data={data?.data ?? []}
        isLoading={isLoading}
        error={error}
        onRetry={() => void refetch()}
        onRowClick={setHistory}
        emptyTitle="No Charges"
        emptyDescription="A late cancellation the customer asked for shows up here."
      />
      {data?.meta && <Pager page={page} totalPages={data.meta.total_pages} total={data.meta.total} onPage={setPage} busy={isFetching} />}
      <ChargeAdjustDialog charge={adjusting?.charge ?? null} mode={adjusting?.mode ?? "reduce"} onClose={() => setAdjusting(null)} />
      <ChargeHistoryDialog charge={history} onClose={() => setHistory(null)} />
    </div>
  );
}

/** The customer drawer's block: charges that can still be reduced or
 *  waived (on the wallet, or on an unpaid booking), with the same actions.
 *  Hidden when there are none. */
export function CustomerChargesPanel({ customerId }: { customerId: string }) {
  const [adjusting, setAdjusting] = useState<{ charge: CustomerCharge; mode: "reduce" | "waive" } | null>(null);
  const [history, setHistory] = useState<CustomerCharge | null>(null);
  const { data, isLoading, isError, isFetching, refetch } = useQuery({
    queryKey: ["charges", "customer", customerId],
    queryFn: () => chargeApi.list({ customer_id: customerId, page: 1, page_size: 20 }),
  });
  const live = (data?.data ?? []).filter(canAdjustCharge);
  if (isLoading) return <Spinner className="h-4 w-4" />;
  if (isError && !data) return <ErrorState className="p-4" message="Couldn't load cancellation charges." busy={isFetching} onRetry={() => void refetch()} />;
  if (!live.length) return null;
  const total = live.reduce((sum, c) => sum + (Number(c.amount) || 0), 0);
  return (
    <div className="mt-5" data-testid="customer-charges">
      <p className="mb-2 text-xs font-semibold text-gray-500">Cancellation Charges · ₹{Math.round(total)}</p>
      <div className="divide-y divide-[#EEF2F7] rounded-xl border border-[#E4E9F1]">
        {live.map((c) => (
          <div key={c.id} className="flex flex-wrap items-center justify-between gap-2 px-3.5 py-2.5">
            <div className="min-w-0">
              <p className="text-sm font-medium text-black">
                <AmountCell c={c} /> <span className="font-normal text-gray-500">· {TIER_LABELS[c.tier] || c.tier}</span>
              </p>
              <p className="text-xs text-gray-500">
                Cancelled <span className="font-mono-num">{c.source_booking_number || "—"}</span>
                {c.status === "applied" && c.applied_to_booking_number ? (
                  <>
                    {" "}
                    · On <span className="font-mono-num">{c.applied_to_booking_number}</span>
                  </>
                ) : c.status === "settled" ? (
                  " · Added to wallet as due"
                ) : (
                  " · Added to their next booking"
                )}
              </p>
            </div>
            <ChargeActions c={c} onAdjust={(charge, mode) => setAdjusting({ charge, mode })} onHistory={setHistory} />
          </div>
        ))}
      </div>
      <ChargeAdjustDialog charge={adjusting?.charge ?? null} mode={adjusting?.mode ?? "reduce"} onClose={() => setAdjusting(null)} />
      <ChargeHistoryDialog charge={history} onClose={() => setHistory(null)} />
    </div>
  );
}
