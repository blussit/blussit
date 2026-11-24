import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, Coins, MinusCircle, Pencil, PlusCircle, Undo2 } from "lucide-react";
import { Badge, Button, ErrorState, Input, Modal } from "../ui";
import { newIdempotencyKey, payrollApi, type BookingCaptainMoney, type CommissionRow } from "../../api/payroll";
import { getErrorMessage } from "../../lib/api-client";
import { formatDateTime } from "../../lib/date";
import { cleanAmountInput, rupeesText, toPaise } from "../../lib/paise";
import { BasisBadge, SkeletonRows } from "../payroll/payrollUi";
import { CommissionEditForm } from "../payroll/CommissionsTab";
import { ReceiveCashModal } from "../payroll/CashFromCaptain";
import { usePayrollWrite } from "../payroll/usePayrollWrite";

/**
 * Everything about the captain's money on ONE booking, in one place and in
 * plain words: his commission (set it, mark it paid), the cash he took at the
 * door (record that he handed it in), and money added to or taken from him for
 * this job. Each action saves straight away, with its own reason.
 */
export function CaptainMoneyPanel({ bookingId }: { bookingId: string }) {
  const query = useQuery({ queryKey: ["payroll-booking-money", bookingId], queryFn: () => payrollApi.bookingCaptainMoney(bookingId) });
  if (query.isLoading) return <SkeletonRows rows={3} />;
  if (query.isError || !query.data) return <ErrorState message="Couldn't load this captain's money." busy={query.isFetching} onRetry={() => void query.refetch()} />;
  return <Body data={query.data} />;
}

export function CaptainMoneyDialog({ bookingId, bookingNumber, open, onClose }: { bookingId: string; bookingNumber: string; open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} title={`Captain Money — ${bookingNumber}`} maxWidth="max-w-2xl">
      {open && <CaptainMoneyPanel bookingId={bookingId} />}
    </Modal>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-gray-100 p-3">
      <p className="mb-2 text-sm font-semibold text-[var(--color-text-primary)]">{title}</p>
      {children}
    </div>
  );
}

function Body({ data }: { data: BookingCaptainMoney }) {
  const write = usePayrollWrite();
  const [editing, setEditing] = useState<CommissionRow | null>(null);
  const [receiving, setReceiving] = useState(false);
  const [entry, setEntry] = useState<"bonus" | "deduction" | null>(null);

  // A salary captain earns no commission, even on a job a manager logged for him.
  const c = data.basis === "salary" && !data.commission?.mode ? null : data.commission;
  const mark = useMutation({
    mutationFn: (paid: boolean) =>
      paid
        ? payrollApi.markPaid(data.captain_id!, { booking_ids: [data.booking_id], reason: "Already paid" })
        : payrollApi.markUnpaid(data.captain_id!, { booking_ids: [data.booking_id], reason: "Marked unpaid from the booking" }),
    onSuccess: (res) => write.ok("Done", res.message),
    onError: (err) => write.fail(err),
  });

  if (!data.captain_id) return <p className="text-sm text-[var(--color-text-secondary)]">No captain is on this booking yet.</p>;

  const unpaid = c && (c.payment_state === "unpaid" || c.payment_state === "part_paid") && !c.not_set;
  return (
    <div className="space-y-3" data-testid="captain-money">
      <p className="flex flex-wrap items-center gap-2 text-sm text-[var(--color-text-secondary)]">
        <span className="font-semibold text-[var(--color-text-primary)]">{data.captain_name || "Captain"}</span>
        {data.basis && <BasisBadge basis={data.basis} />}
        {!data.finance_impact && <Badge tone="neutral">Paid By Franchise</Badge>}
      </p>

      <Card title="Commission On This Booking">
        {c ? (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="font-mono-num text-lg font-bold text-[var(--color-text-primary)]">{c.not_set ? "Not Set Yet" : rupeesText(c.amount_paise)}</p>
              <p className="text-xs text-[var(--color-text-secondary)]">
                {c.not_set
                  ? "Set what the captain earns for this job."
                  : c.payment_state === "paid"
                    ? c.invoice_numbers?.length ? `Paid — invoice ${c.invoice_numbers.join(", ")}` : "Paid — marked as already paid"
                    : c.payment_state === "part_paid"
                      ? `Part paid — ${rupeesText(c.unsettled_paise || 0)} still unpaid`
                      : "Not paid yet"}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={() => setEditing(c)} data-testid="cm-set-commission">
                <Pencil className="h-3.5 w-3.5" /> {c.not_set ? "Set Commission" : "Change Commission"}
              </Button>
              {unpaid && (
                <Button size="sm" variant="outline" isLoading={mark.isPending} onClick={() => mark.mutate(true)} data-testid="cm-mark-paid">
                  <CheckCircle2 className="h-3.5 w-3.5" /> Mark As Paid
                </Button>
              )}
              {(c.marked_paise ?? 0) > 0 && (
                <Button size="sm" variant="outline" isLoading={mark.isPending} onClick={() => mark.mutate(false)} data-testid="cm-mark-unpaid">
                  <Undo2 className="h-3.5 w-3.5" /> Mark Unpaid
                </Button>
              )}
            </div>
          </div>
        ) : (
          <p className="text-sm text-[var(--color-text-secondary)]">
            {data.basis === "salary" ? "This captain is on a salary — a booking earns no commission." : data.status !== "completed" ? "Commission appears once the job is completed." : "This booking has no commission."}
          </p>
        )}
      </Card>

      <Card title="Cash The Captain Took">
        {data.cash ? (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="font-mono-num text-lg font-bold text-[var(--color-text-primary)]">{rupeesText(data.cash.held_paise)} <span className="text-xs font-normal text-[var(--color-text-secondary)]">with him</span></p>
              <p className="text-xs text-[var(--color-text-secondary)]">
                Took {rupeesText(data.cash.collected_paise)} from the customer · handed in {rupeesText(data.cash.returned_paise)}
              </p>
            </div>
            <Button size="sm" disabled={data.cash.held_paise <= 0} onClick={() => setReceiving(true)} data-testid="cm-cash-received">
              <Coins className="h-3.5 w-3.5" /> {data.cash.held_paise > 0 ? "Cash Received From Captain" : "All Cash Handed In"}
            </Button>
          </div>
        ) : (
          <p className="text-sm text-[var(--color-text-secondary)]">The captain did not take any cash for this booking.</p>
        )}
      </Card>

      <Card title="Add Or Take Money">
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" onClick={() => setEntry("bonus")} data-testid="cm-add-money">
            <PlusCircle className="h-3.5 w-3.5" /> Add Money To Captain
          </Button>
          <Button size="sm" variant="outline" onClick={() => setEntry("deduction")} data-testid="cm-take-money">
            <MinusCircle className="h-3.5 w-3.5" /> Take Money From Captain
          </Button>
        </div>
        <p className="mt-2 text-xs text-[var(--color-text-secondary)]">Added money is owed to him with his next pay; taken money is cut from it. Both are recorded against this booking.</p>
        {data.adjustments.length > 0 && (
          <ul className="mt-2 divide-y divide-gray-100 rounded-lg border border-gray-100 text-sm">
            {data.adjustments.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-1.5">
                <span className="min-w-0 text-[var(--color-text-primary)]">
                  {a.note || a.kind}
                  <span className="text-xs text-[var(--color-text-secondary)]"> · {formatDateTime(a.created_at)}{a.actor_name ? ` · ${a.actor_name}` : ""}</span>
                </span>
                <span className={`font-mono-num font-semibold ${a.amount_paise < 0 ? "text-[var(--color-error)]" : ""}`}>{a.amount_paise > 0 ? "+" : "−"}{rupeesText(Math.abs(a.amount_paise))}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Modal open={!!editing} onClose={() => setEditing(null)} title={editing ? `Commission — ${editing.booking_number}` : "Commission"}>
        {editing && <CommissionEditForm row={editing} onClose={() => setEditing(null)} />}
      </Modal>
      {data.cash && (
        <ReceiveCashModal
          captainId={data.cash.captain_id} captainName={data.captain_name || "The captain"} heldPaise={data.cash.held_paise}
          bookingId={data.booking_id} bookingNumber={data.booking_number} open={receiving} onClose={() => setReceiving(false)}
        />
      )}
      <Modal open={!!entry} onClose={() => setEntry(null)} title={entry === "bonus" ? "Add Money To Captain" : "Take Money From Captain"}>
        {entry && <EntryForm data={data} kind={entry} onClose={() => setEntry(null)} />}
      </Modal>
    </div>
  );
}

function EntryForm({ data, kind, onClose }: { data: BookingCaptainMoney; kind: "bonus" | "deduction"; onClose: () => void }) {
  const write = usePayrollWrite();
  const [key] = useState(newIdempotencyKey);
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const paise = toPaise(amount) || 0;
  const mutation = useMutation({
    mutationFn: () => payrollApi.adjust(data.captain_id!, { kind, amount_paise: paise, note: note.trim(), booking_id: data.booking_id, idempotency_key: key }),
    onSuccess: (res) => {
      write.ok(kind === "bonus" ? "Money Added" : "Money Taken", res.message);
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
        if (note.trim().length < 3) return setError("Say why — a short reason is required.");
        if (!mutation.isPending) mutation.mutate();
      }}
    >
      <p className="text-sm text-[var(--color-text-secondary)]">
        {kind === "bonus"
          ? `Adds to what ${data.captain_name || "the captain"} is owed, for ${data.booking_number}.`
          : `Cuts from what ${data.captain_name || "the captain"} is owed, for ${data.booking_number}.`}
      </p>
      <Input label="Amount (₹)" inputMode="numeric" value={amount} onChange={(e) => setAmount(cleanAmountInput(e.target.value))} autoFocus />
      <Input label="Reason" maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} placeholder={kind === "bonus" ? "Extra heavy cleaning" : "Damaged a mirror"} />
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
          {kind === "bonus" ? "Add Money" : "Take Money"}
        </Button>
      </div>
    </form>
  );
}
