import { useEffect, useMemo, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDownLeft, ArrowUpRight, HandCoins, SlidersHorizontal, Wallet } from "lucide-react";
import {
  customerWalletApi,
  newIdempotencyKey,
  PAYBACK_REASON_LABELS,
  PAYOUT_METHOD_LABELS,
  type PaybackReason,
  type PayoutMethod,
  type WalletEntry,
  type WalletPayoutRow,
} from "../../api/customerWallet";
import { adminServiceCenterApi } from "../../api/admin";
import { crmApi } from "../../api/crm";
import type { StaffBooking } from "../../api/staffBookings";
import { useAuth } from "../../context/AuthContext";
import { useToast } from "../../context/ToastContext";
import { getErrorMessage } from "../../lib/api-client";
import { asUtcInstant, formatDateTime } from "../../lib/date";
import { toTitle } from "../../lib/titleCase";
import { Button, DataTable, ErrorState, Input, Modal, Select, Spinner, type Column } from "../ui";
import { Pager } from "./ListControls";
import { PaybackDialog, paybackOption, paybackSplitLine } from "./PaybackDialog";

/**
 * The customer's wallet as staff see it (spec §1.1): balance (green when the
 * customer has credit, red when they owe), the recent ledger, and — manager
 * or admin — "Pay Back To Customer" (MONEY-2: money the manager paid from
 * his side for one cancelled / delayed / problem booking). Admin also gets
 * "Adjust" (the only free correction, either way). A manager sees another
 * center's entries greyed, as "Other Center", without booking details.
 */

/** Ledger kinds in staff words (Title Case, the customer's label is "you"-worded). */
const KIND_LABELS: Record<string, string> = {
  cancellation: "Booking Cancelled",
  cancellation_charge: "Late Cancel Charge",
  charge_migrated: "Late Cancel Charge",
  charge_reduced: "Charge Reduced",
  price_reduced: "Price Reduced",
  overpayment: "Extra Payment Kept",
  booking_payment: "Used For Booking",
  previous_balance_paid: "Due Paid",
  payout: "Paid Back By Manager",
  adjustment: "Admin Adjustment",
};

export const walletKindLabel = (e: Pick<WalletEntry, "kind" | "label">) => KIND_LABELS[e.kind] || toTitle(e.label) || "Wallet";

/** "+₹120" / "−₹50" — whole rupees. */
export const signedRupees = (n: number) => `${n < 0 ? "−" : "+"}₹${Math.abs(Math.round(n)).toLocaleString("en-IN")}`;
const rupees = (n: number) => `₹${Math.abs(Math.round(n)).toLocaleString("en-IN")}`;

function who(e: WalletEntry): string {
  if (e.actor_name) return e.actor_role && e.actor_role !== "customer" ? `${e.actor_name} (${toTitle(e.actor_role)})` : e.actor_name;
  if (e.actor_role === "system" || !e.actor_role) return "System";
  return toTitle(e.actor_role);
}

const methodLabel = (m?: string | null) => (m ? PAYOUT_METHOD_LABELS[m as PayoutMethod] || toTitle(m) : "");
const reasonLabel = (e: { reason?: string | null; reason_label?: string | null }) =>
  e.reason_label || (e.reason ? PAYBACK_REASON_LABELS[e.reason as PaybackReason] || toTitle(e.reason) : "");

function LedgerRow({ e }: { e: WalletEntry }) {
  const credit = e.amount >= 0;
  // A manager's view of another center's entry: amount / kind / date only.
  const other = e.own_center === false;
  const bookingTag = other ? e.booking_label || "Other Center" : e.booking_number || null;
  const paybackLine = e.kind === "payout" && !other ? [reasonLabel(e), methodLabel(e.method), e.reference ? `Ref ${e.reference}` : ""].filter(Boolean).join(" · ") : "";
  return (
    <li className={`flex items-start justify-between gap-3 px-3.5 py-2.5 ${other ? "bg-gray-50 opacity-60" : ""}`} data-testid="wallet-ledger-row" data-other-center={other || undefined}>
      <div className="flex min-w-0 items-start gap-2.5">
        <span
          className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${credit ? "bg-[var(--ui-success-bg,#E7F6EC)] text-[var(--color-success)]" : "bg-red-50 text-[var(--color-error)]"}`}
        >
          {credit ? <ArrowDownLeft className="h-3.5 w-3.5" /> : <ArrowUpRight className="h-3.5 w-3.5" />}
        </span>
        <div className="min-w-0">
          <p className="text-sm font-medium text-black">
            {walletKindLabel(e)}
            {bookingTag ? <span className={`font-normal text-gray-500 ${other ? "" : "font-mono-num"}`}> · {bookingTag}</span> : null}
          </p>
          <p className="text-xs text-gray-500">
            {[other ? "" : who(e), e.created_at ? formatDateTime(asUtcInstant(e.created_at)) : ""].filter(Boolean).join(" · ")}
          </p>
          {paybackLine && <p className="text-xs text-gray-500">{paybackLine}</p>}
          {e.note && !other && <p className="break-words text-xs text-gray-600">&ldquo;{e.note}&rdquo;</p>}
        </div>
      </div>
      <div className="shrink-0 text-right">
        <p className={`font-mono-num text-sm font-semibold ${credit ? "text-[var(--color-success)]" : "text-[var(--color-error)]"}`}>{signedRupees(e.amount)}</p>
        <p className="font-mono-num text-[11px] text-gray-400">Bal {e.balance_after < 0 ? "−" : ""}{rupees(e.balance_after)}</p>
      </div>
    </li>
  );
}

const PAGE_SIZE = 8;

/** The drawer block: balance + ledger + actions. */
export function CustomerWalletPanel({ customerId, customerName }: { customerId: string; customerName?: string | null }) {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [page, setPage] = useState(1);
  const [payoutOpen, setPayoutOpen] = useState(false);
  const [adjustOpen, setAdjustOpen] = useState(false);
  useEffect(() => setPage(1), [customerId]);
  const { data, isLoading, isError, isFetching, refetch } = useQuery({
    queryKey: ["customer-wallet", customerId, page],
    queryFn: () => customerWalletApi.forCustomer(customerId, { page, page_size: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });

  if (isLoading) return <Spinner className="mt-5 h-4 w-4" />;
  if (isError && !data) return <ErrorState className="mt-5 p-4" message="Couldn't load the wallet." busy={isFetching} onRetry={() => void refetch()} />;
  if (!data) return null;

  const balance = Math.round(data.balance);
  const totalPages = Math.max(1, Math.ceil((data.total || 0) / PAGE_SIZE));
  return (
    <div className="mt-5" data-testid="customer-wallet">
      <p className="mb-2 text-xs font-semibold text-gray-500">Wallet</p>
      <div className="rounded-xl border border-[#F3E5B5]">
        <div className="flex flex-wrap items-center justify-between gap-3 px-3.5 py-3">
          <div className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-full bg-gray-100 text-black">
              <Wallet className="h-4 w-4" />
            </span>
            <div>
              <p
                className={`font-mono-num text-xl font-bold ${balance > 0 ? "text-[var(--color-success)]" : balance < 0 ? "text-[var(--color-error)]" : "text-black"}`}
                data-testid="wallet-balance"
              >
                {balance < 0 ? "−" : ""}
                {rupees(balance)}
              </p>
              <p className="text-xs text-gray-500">
                {balance > 0
                  ? "Credit — used on their next booking"
                  : balance < 0
                    ? data.previous_balance_due > 0
                      ? `Owes ${rupees(data.previous_balance_due)} — added to their next booking`
                      : "Owes — already added to an unpaid booking"
                    : "No balance"}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" className="min-h-11 sm:min-h-0" onClick={() => setPayoutOpen(true)}>
              <HandCoins className="h-3.5 w-3.5" /> Pay Back To Customer
            </Button>
            {isAdmin && (
              <Button size="sm" variant="outline" className="min-h-11 sm:min-h-0" onClick={() => setAdjustOpen(true)}>
                <SlidersHorizontal className="h-3.5 w-3.5" /> Adjust
              </Button>
            )}
          </div>
        </div>
        {data.items.length > 0 ? (
          <ul className={`divide-y divide-[#FAF3DF] border-t border-[#F3E5B5] ${isFetching ? "opacity-60" : ""}`}>
            {data.items.map((e) => (
              <LedgerRow key={e.id} e={e} />
            ))}
          </ul>
        ) : (
          <p className="border-t border-[#F3E5B5] px-3.5 py-3 text-sm text-gray-500">No wallet activity yet.</p>
        )}
        {totalPages > 1 && (
          <div className="border-t border-[#F3E5B5] px-3.5 py-2">
            <Pager page={page} totalPages={totalPages} total={data.total} onPage={setPage} busy={isFetching} />
          </div>
        )}
      </div>
      <WalletPaybackDialog open={payoutOpen} onClose={() => setPayoutOpen(false)} customerId={customerId} customerName={customerName} />
      {isAdmin && <AdjustWalletDialog open={adjustOpen} onClose={() => setAdjustOpen(false)} customerId={customerId} customerName={customerName} balance={data.balance} />}
    </div>
  );
}

function invalidateWallet(queryClient: ReturnType<typeof useQueryClient>, customerId: string) {
  queryClient.invalidateQueries({ queryKey: ["customer-wallet", customerId] });
  queryClient.invalidateQueries({ queryKey: ["wallet-payouts"] });
}

/** Pay back from the wallet panel: the booking is picked from the
 *  customer's own bookings (this center's, for a manager) — cancelled,
 *  delayed, flagged or with a complaint first. The server decides. */
function WalletPaybackDialog({ open, onClose, customerId, customerName }: { open: boolean; onClose: () => void; customerId: string; customerName?: string | null }) {
  const { data, isLoading } = useQuery({
    queryKey: ["customer-360", customerId],
    queryFn: () => crmApi.customer360(customerId),
    enabled: open,
  });
  const options = useMemo(() => {
    const complained = new Set((data?.complaints ?? []).map((c) => (c as { booking_id?: string | null }).booking_id).filter(Boolean) as string[]);
    return (data?.bookings ?? [])
      .map((b) => paybackOption(b as StaffBooking, complained.has(b.id)))
      .filter((o) => o.signals.length > 0);
  }, [data]);
  return (
    <PaybackDialog
      open={open}
      onClose={onClose}
      customerId={customerId}
      customerName={customerName}
      bookings={options}
      loadingBookings={isLoading}
    />
  );
}

/** Admin correction, either direction, with a note. */
export function AdjustWalletDialog({
  open,
  onClose,
  customerId,
  customerName,
  balance,
}: {
  open: boolean;
  onClose: () => void;
  customerId: string;
  customerName?: string | null;
  balance: number;
}) {
  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const [direction, setDirection] = useState<"credit" | "debit">("credit");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [key, setKey] = useState(() => newIdempotencyKey("adj"));
  useEffect(() => {
    if (!open) return;
    setDirection("credit");
    setAmount("");
    setNote("");
    setError("");
    setKey(newIdempotencyKey("adj"));
  }, [open]);

  const value = amount.trim() === "" ? NaN : Math.round(Number(amount));
  const ok = Number.isFinite(value) && value >= 1 && note.trim().length >= 3;
  const signed = direction === "credit" ? value : -value;
  const save = useMutation({
    mutationFn: () => customerWalletApi.adjust(customerId, { amount: signed, note: note.trim(), idempotency_key: key }),
    onSuccess: ({ entry, message }) => {
      invalidateWallet(queryClient, customerId);
      pushToast({
        tone: entry.created ? "success" : "info",
        title: entry.created ? `Wallet ${direction === "credit" ? "Credited" : "Debited"} ₹${value}` : "Already Applied",
        message: entry.created ? `New balance ₹${Math.round(entry.balance)}.` : message,
      });
      onClose();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  return (
    <Modal open={open} onClose={onClose} title="Adjust Wallet" maxWidth="max-w-sm">
      <div className="space-y-4">
        <p className="text-sm text-[var(--color-text-secondary)]">
          {customerName || "Customer"} · balance{" "}
          <span className={`font-mono-num font-semibold ${balance < 0 ? "text-[var(--color-error)]" : "text-[#0E1A33]"}`}>
            {balance < 0 ? "−" : ""}
            {rupees(balance)}
          </span>
          . The customer is told on WhatsApp.
        </p>
        <div className="grid grid-cols-2 gap-2" role="radiogroup">
          {(
            [
              { id: "credit", label: "Add Credit" },
              { id: "debit", label: "Take Off" },
            ] as const
          ).map((o) => (
            <button
              key={o.id}
              type="button"
              role="radio"
              aria-checked={direction === o.id}
              onClick={() => setDirection(o.id)}
              className={`min-h-11 rounded-xl border px-3 text-sm font-medium ${
                direction === o.id ? "border-black bg-[var(--color-primary-light)] text-black" : "border-gray-200 text-gray-600 hover:border-gray-400"
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>
        <Input label="Amount (₹)" inputMode="numeric" value={amount} onChange={(e) => setAmount(e.target.value.replace(/\D/g, "").slice(0, 7))} />
        <Input
          label="Reason"
          maxLength={300}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="e.g. Goodwill credit for a late captain"
          error={note.trim() !== "" && note.trim().length < 3 ? "At least 3 characters." : undefined}
        />
        {Number.isFinite(value) && value >= 1 && (
          <p className="text-sm text-[var(--color-text-secondary)]">
            New balance:{" "}
            <span className="font-mono-num font-semibold text-[#0E1A33]">
              {balance + signed < 0 ? "−" : ""}
              {rupees(balance + signed)}
            </span>
          </p>
        )}
        {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
        <div className="flex gap-2">
          <Button variant="outline" className="min-h-11 flex-1" onClick={onClose}>
            Back
          </Button>
          <Button className="min-h-11 flex-1" isLoading={save.isPending} disabled={!ok} onClick={() => save.mutate()}>
            {direction === "credit" ? "Add Credit" : "Take Off"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

const SOURCE_LABELS: Record<string, string> = {
  manager_payback: "Payback",
  wallet_payout: "Older Payout",
  razorpay_refund: "Razorpay Refund",
};

/** Admin "Paybacks": every manager payback (wallet + paid by the manager)
 *  and older wallet payouts, newest first — by center and paid-date range. */
export function PaybacksList({ onOpenCustomer }: { onOpenCustomer?: (customerId: string) => void }) {
  const [page, setPage] = useState(1);
  const [centerId, setCenterId] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  useEffect(() => setPage(1), [centerId, dateFrom, dateTo]);
  const { data: centers } = useQuery({ queryKey: ["service-centers-all"], queryFn: () => adminServiceCenterApi.list({ page: 1, page_size: 100 }) });
  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: ["wallet-payouts", page, centerId, dateFrom, dateTo],
    queryFn: () =>
      customerWalletApi.payouts({
        page,
        page_size: 25,
        service_center_id: centerId || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
      }),
    placeholderData: keepPreviousData,
  });
  const columns: Column<WalletPayoutRow>[] = [
    {
      header: "Customer",
      accessor: (r) => (
        <div className="min-w-0">
          <p className="font-medium">{r.customer_name || "—"}</p>
          <p className="text-xs text-[var(--color-text-secondary)]">
            {[r.booking_number, r.center_name].filter(Boolean).join(" · ") || r.customer_phone || "—"}
          </p>
        </div>
      ),
    },
    {
      header: "Amount",
      accessor: (r) => (
        <div>
          <p className="font-mono-num font-semibold">{rupees(r.amount)}</p>
          {r.goodwill_amount > 0 || r.wallet_amount > 0 ? (
            <p className="whitespace-nowrap text-xs text-[var(--color-text-secondary)]">{paybackSplitLine(r.wallet_amount, r.goodwill_amount, "Manager")}</p>
          ) : null}
        </div>
      ),
    },
    {
      header: "Reason",
      accessor: (r) => (
        <div className="text-xs">
          <p className="font-medium text-[var(--ui-ink,#0E1A33)]">{reasonLabel(r) || SOURCE_LABELS[r.source || ""] || "—"}</p>
          {r.note && <p className="max-w-[16rem] break-words text-[var(--color-text-secondary)]">{r.note}</p>}
          {r.source && r.source !== "manager_payback" && <p className="text-[var(--color-text-secondary)]">{SOURCE_LABELS[r.source] || toTitle(r.source)}</p>}
        </div>
      ),
    },
    {
      header: "Method",
      accessor: (r) => (
        <div className="text-xs">
          <p>{methodLabel(r.method) || "—"}</p>
          {r.reference && <p className="font-mono-num text-[var(--color-text-secondary)]">Ref {r.reference}</p>}
        </div>
      ),
    },
    {
      header: "Paid By",
      accessor: (r) => (
        <div className="text-xs text-[var(--color-text-secondary)]">
          <p className="font-medium text-[var(--ui-ink,#0E1A33)]">
            {r.paid_by_name || "—"}
            {r.paid_by_role && r.paid_by_role !== "manager" ? ` (${toTitle(r.paid_by_role)})` : ""}
          </p>
          <p className="whitespace-nowrap">{r.created_at ? formatDateTime(asUtcInstant(r.created_at)) : "—"}</p>
        </div>
      ),
    },
  ];
  const filtered = !!(centerId || dateFrom || dateTo);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3" data-testid="paybacks-filters">
        <Select label="Center" value={centerId} onChange={(e) => setCenterId(e.target.value)}>
          <option value="">All Centers</option>
          {(centers?.data ?? []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </Select>
        <Input type="date" label="From" value={dateFrom} max={dateTo || undefined} onChange={(e) => setDateFrom(e.target.value)} />
        <Input type="date" label="To" value={dateTo} min={dateFrom || undefined} onChange={(e) => setDateTo(e.target.value)} />
      </div>
      <DataTable<WalletPayoutRow>
        columns={columns}
        data={data?.data ?? []}
        isLoading={isLoading}
        error={error}
        onRetry={() => void refetch()}
        onRowClick={onOpenCustomer ? (r) => onOpenCustomer(r.customer_id) : undefined}
        emptyTitle="No Paybacks"
        emptyDescription={filtered ? "Nothing for these filters." : "Money managers paid back to customers for a cancelled, delayed or problem booking shows up here."}
      />
      {data?.meta && <Pager page={page} totalPages={data.meta.total_pages} total={data.meta.total} onPage={setPage} busy={isFetching} />}
    </div>
  );
}
