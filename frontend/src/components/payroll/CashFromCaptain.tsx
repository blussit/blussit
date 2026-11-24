import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Coins, Undo2 } from "lucide-react";
import { Badge, Button, ErrorState, Input, Modal } from "../ui";
import { payrollApi, newIdempotencyKey, type CashReceipt } from "../../api/payroll";
import { getErrorMessage } from "../../lib/api-client";
import { formatDateTime } from "../../lib/date";
import { cleanAmountInput, rupeesInput, rupeesText, toPaise } from "../../lib/paise";
import { SkeletonRows } from "./payrollUi";
import { usePayrollWrite } from "./usePayrollWrite";

/**
 * "Cash With Captain": what he took from customers at the door and has not
 * handed in yet, with one button to record that he did. Used on the captain's
 * Summary; the same dialog is used from a single booking.
 */
export function CashFromCaptainCard({ captainId }: { captainId: string }) {
  const [receiving, setReceiving] = useState(false);
  const [undoing, setUndoing] = useState<CashReceipt | null>(null);
  const query = useQuery({ queryKey: ["payroll-cash", captainId], queryFn: () => payrollApi.cash(captainId) });
  if (query.isLoading) return <SkeletonRows rows={2} />;
  if (query.isError || !query.data) return <ErrorState message="Couldn't load the cash this captain holds." busy={query.isFetching} onRetry={() => void query.refetch()} />;
  const cash = query.data;
  const receipts = cash.receipts.filter((r) => r.kind === "received").slice(0, 4);

  return (
    <div className="rounded-2xl border border-gray-100 p-4" data-testid="cash-with-captain">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="flex items-center gap-2 text-sm font-semibold text-[var(--color-text-primary)]">
            <Coins className="h-4 w-4 text-[#5F6878]" /> Cash With Captain
          </p>
          <p className="mt-0.5 font-mono-num text-xl font-bold text-[var(--color-text-primary)]">{rupeesText(cash.held_paise)}</p>
          <p className="text-xs text-[var(--color-text-secondary)]">
            {cash.held_paise > 0 ? `Taken from customers across ${cash.bookings.length} ${cash.bookings.length === 1 ? "booking" : "bookings"}, not handed in yet.` : "He has handed in all the cash he took."}
          </p>
        </div>
        <Button size="sm" disabled={cash.held_paise <= 0} onClick={() => setReceiving(true)}>
          <Coins className="h-4 w-4" /> Cash Received From Captain
        </Button>
      </div>
      {receipts.length > 0 && (
        <ul className="mt-3 divide-y divide-gray-100 rounded-xl border border-gray-100 text-sm">
          {receipts.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
              <span className={r.status === "void" ? "text-[var(--color-text-secondary)] line-through" : "text-[var(--color-text-primary)]"}>
                {rupeesText(r.amount_paise)} received · {formatDateTime(r.created_at)}
                {r.actor_name ? ` · ${r.actor_name}` : ""}
                {r.allocations.length ? ` · ${r.allocations.map((a) => a.booking_number).filter(Boolean).slice(0, 3).join(", ")}${r.allocations.length > 3 ? "…" : ""}` : ""}
              </span>
              {r.status === "void" ? (
                <Badge tone="error">Undone</Badge>
              ) : (
                <Button size="sm" variant="ghost" aria-label={`Undo cash received ${rupeesText(r.amount_paise)}`} onClick={() => setUndoing(r)}>
                  <Undo2 className="h-3.5 w-3.5" /> Undo
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      <ReceiveCashModal captainId={captainId} captainName={cash.captain_name || "The captain"} heldPaise={cash.held_paise} open={receiving} onClose={() => setReceiving(false)} />
      <UndoReceiptModal receipt={undoing} onClose={() => setUndoing(null)} />
    </div>
  );
}

/** Record cash handed in. With `bookingId` only that booking's cash; without, the oldest bookings first. */
export function ReceiveCashModal({
  captainId, captainName, heldPaise, bookingId, bookingNumber, open, onClose,
}: {
  captainId: string; captainName: string; heldPaise: number; bookingId?: string; bookingNumber?: string; open: boolean; onClose: () => void;
}) {
  return (
    <Modal open={open} onClose={onClose} title={bookingNumber ? `Cash Received — ${bookingNumber}` : `Cash Received From ${captainName}`}>
      <ReceiveForm captainId={captainId} heldPaise={heldPaise} bookingId={bookingId} onClose={onClose} />
    </Modal>
  );
}

function ReceiveForm({ captainId, heldPaise, bookingId, onClose }: { captainId: string; heldPaise: number; bookingId?: string; onClose: () => void }) {
  const write = usePayrollWrite();
  const [key] = useState(newIdempotencyKey);
  const [amount, setAmount] = useState(rupeesInput(heldPaise) || (heldPaise > 0 ? "1" : ""));
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const typed = toPaise(amount) || 0;
  // Whole rupees are typed; a few paise left over (₹216.20 shown as ₹216) are received with the rest.
  const paise = typed > 0 && Math.abs(typed - heldPaise) < 100 ? heldPaise : typed;
  const mutation = useMutation({
    mutationFn: () => payrollApi.cashReceived(captainId, { amount_paise: paise, booking_id: bookingId, note: note.trim() || undefined, idempotency_key: key }),
    onSuccess: (res) => {
      write.ok("Cash Recorded", res.message);
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
        if (paise <= 0) return setError("Enter the amount in whole rupees.");
        if (paise > heldPaise) return setError(`He holds only ${rupeesText(heldPaise)}${bookingId ? " for this booking" : ""}.`);
        if (!mutation.isPending) mutation.mutate();
      }}
    >
      <p className="text-sm text-[var(--color-text-secondary)]">
        He holds <span className="font-mono-num font-semibold text-[var(--color-text-primary)]">{rupeesText(heldPaise)}</span>
        {bookingId ? " from this booking" : ""}. Record what he actually handed over{bookingId ? "" : " — the oldest bookings are marked as handed in first"}. You can undo it.
      </p>
      <Input label="Amount Received (₹)" inputMode="numeric" value={amount} onChange={(e) => setAmount(cleanAmountInput(e.target.value))} autoFocus />
      <Input label="Note (Optional)" maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Evening handover" />
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
          Record Cash Received
        </Button>
      </div>
    </form>
  );
}

function UndoReceiptModal({ receipt, onClose }: { receipt: CashReceipt | null; onClose: () => void }) {
  return (
    <Modal open={!!receipt} onClose={onClose} title="Undo Cash Received?">
      {receipt && <UndoForm receipt={receipt} onClose={onClose} />}
    </Modal>
  );
}

function UndoForm({ receipt, onClose }: { receipt: CashReceipt; onClose: () => void }) {
  const write = usePayrollWrite();
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const mutation = useMutation({
    mutationFn: () => payrollApi.voidCashReceipt(receipt.id, reason.trim()),
    onSuccess: (res) => {
      write.ok("Cash Receipt Undone", res.message);
      onClose();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });
  return (
    <div className="space-y-4">
      <p className="text-sm text-[var(--color-text-secondary)]">
        The {rupeesText(receipt.amount_paise)} goes back to what the captain holds. The receipt stays on record, marked undone.
      </p>
      <Input label="Reason" maxLength={200} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Counted wrong" autoFocus />
      {error && (
        <p role="alert" className="text-sm text-[var(--color-error)]">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button variant="outline" className="flex-1" onClick={onClose}>
          Keep It
        </Button>
        <Button variant="danger" className="flex-1" disabled={reason.trim().length < 3} isLoading={mutation.isPending} onClick={() => mutation.mutate()}>
          Undo
        </Button>
      </div>
    </div>
  );
}
