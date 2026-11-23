import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button, Input, Modal, Select, Switch } from "../ui";
import { useConfirm } from "../../context/ConfirmContext";
import { getErrorMessage } from "../../lib/api-client";
import { todayIST } from "../../lib/date";
import { cleanAmountInput, rupeesInWords, rupeesText, toPaise } from "../../lib/paise";
import { newIdempotencyKey, payrollApi, type AdjustmentInput, type PayrollCaptainDetail, type PaymentKind, type PaymentMethod } from "../../api/payroll";
import { Banner, FRANCHISE_BANNER } from "./payrollUi";
import { usePayrollWrite } from "./usePayrollWrite";

/** A single payment at or above this asks "are you sure?" with the amount in words (₹50,000 unless the server says otherwise). */
const DEFAULT_LARGE_PAISE = 5_000_000;

// Salary months and commission periods are paid through the Pay wizard (§7.14) — this form is for the rest.
const KIND_OPTIONS: { value: PaymentKind; label: string }[] = [
  { value: "petrol", label: "Petrol" },
  { value: "advance", label: "Advance" },
  { value: "other", label: "Other" },
];
const METHOD_OPTIONS: { value: PaymentMethod; label: string }[] = [
  { value: "cash", label: "Cash" },
  { value: "upi", label: "UPI" },
  { value: "bank", label: "Bank" },
  { value: "other", label: "Other" },
];

type CaptainLite = Pick<PayrollCaptainDetail, "id" | "full_name" | "dues_paise" | "finance_impact" | "pay"> & {
  cash_held_paise?: number;
  alert_payment_paise?: number;
};

/**
 * Record a payment to a captain. The idempotency key is made when this
 * dialog opens (the form below only mounts while open), so a double tap or
 * a retry after a slow network can never pay twice.
 */
export function RecordPaymentModal({
  captain,
  open,
  onClose,
  defaultKind,
}: {
  captain: CaptainLite;
  open: boolean;
  onClose: () => void;
  defaultKind?: PaymentKind;
}) {
  return (
    <Modal open={open} onClose={onClose} title={`Advance Or Other Payment — ${captain.full_name}`}>
      <PaymentForm captain={captain} onClose={onClose} defaultKind={defaultKind} />
    </Modal>
  );
}

function PaymentForm({ captain, onClose, defaultKind }: { captain: CaptainLite; onClose: () => void; defaultKind?: PaymentKind }) {
  const confirm = useConfirm();
  const write = usePayrollWrite();
  const [key] = useState(newIdempotencyKey);
  const [amount, setAmount] = useState(captain.dues_paise > 0 ? String(Math.round(captain.dues_paise / 100)) : "");
  const [kind, setKind] = useState<PaymentKind>(defaultKind || "advance");
  const [method, setMethod] = useState<PaymentMethod>("cash");
  const [date, setDate] = useState(todayIST());
  const [note, setNote] = useState("");
  const [settle, setSettle] = useState(false);
  const [error, setError] = useState("");

  const cashHeld = Math.max(0, captain.cash_held_paise || 0);
  const amountPaise = toPaise(amount) || 0;
  const offsetPaise = settle ? Math.min(cashHeld, amountPaise) : 0;
  const largeAt = captain.alert_payment_paise || DEFAULT_LARGE_PAISE;

  const mutation = useMutation({
    mutationFn: () =>
      payrollApi.pay(captain.id, {
        amount_paise: amountPaise,
        kind,
        method,
        date,
        note: note.trim() || undefined,
        cash_offset_paise: offsetPaise || undefined,
        idempotency_key: key,
      }),
    onSuccess: (res) => {
      write.ok("Payment Recorded", res.message);
      onClose();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const submit = async () => {
    setError("");
    if (amountPaise <= 0) return setError("Enter the amount in whole rupees.");
    if (!date || date > todayIST()) return setError("Pick today's date or an earlier one.");
    if (kind === "other" && note.trim().length < 3) return setError("Add a short note saying what this payment is for.");
    if (amountPaise >= largeAt) {
      const ok = await confirm({
        title: `Pay ${rupeesText(amountPaise)} To ${captain.full_name}?`,
        message: `${rupeesInWords(amountPaise / 100)}. This is a large payment and the admin is notified.`,
        confirmLabel: "Yes, Record It",
        tone: "default",
      });
      if (!ok) return;
    }
    mutation.mutate();
  };

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (!mutation.isPending) void submit();
      }}
    >
      {!captain.finance_impact && <Banner>{FRANCHISE_BANNER}</Banner>}
      {captain.dues_paise < 0 && <Banner tone="warning">This captain was already paid {rupeesText(Math.abs(captain.dues_paise))} ahead — this payment is another advance.</Banner>}
      <p className="text-sm text-[var(--color-text-secondary)]">
        Dues right now: <span className="font-mono-num font-semibold text-[var(--color-text-primary)]">{rupeesText(captain.dues_paise)}</span>. For salary or commission use Pay. Every payment here also creates an invoice.
      </p>
      <Input label="Amount (₹)" inputMode="numeric" value={amount} onChange={(e) => setAmount(cleanAmountInput(e.target.value))} placeholder="3000" autoFocus />
      {amountPaise > 0 && <p className="-mt-2 text-xs text-[var(--color-text-secondary)]">{rupeesInWords(amountPaise / 100)}</p>}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Select label="Payment For" value={kind} onChange={(e) => setKind(e.target.value as PaymentKind)}>
          {KIND_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </Select>
        <Select label="Paid By" value={method} onChange={(e) => setMethod(e.target.value as PaymentMethod)}>
          {METHOD_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </Select>
      </div>
      <Input type="date" label="Date" value={date} max={todayIST()} onChange={(e) => setDate(e.target.value)} />
      <Input label="Note (Optional)" maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} placeholder="For first half of November" />
      {cashHeld > 0 && (
        <div className="space-y-2">
          <Switch
            checked={settle}
            onChange={setSettle}
            label="Settle Cash Held"
            description={`This captain is holding ${rupeesText(cashHeld)} of customer cash. Switch on to set it off against this payment.`}
          />
          {settle && amountPaise > 0 && (
            <p className="text-xs text-[var(--color-text-secondary)]">
              {rupeesText(offsetPaise)} is settled against cash held, so you hand over <span className="font-mono-num font-semibold text-[var(--color-text-primary)]">{rupeesText(amountPaise - offsetPaise)}</span>.
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
        <Button type="button" variant="outline" className="flex-1" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" className="flex-1" isLoading={mutation.isPending} disabled={amountPaise <= 0}>
          Record Payment
        </Button>
      </div>
    </form>
  );
}

const ADJUST_KINDS: { value: AdjustmentInput["kind"]; label: string; hint: string }[] = [
  { value: "allowance", label: "Allowance", hint: "Petrol or any other expense the captain is owed." },
  { value: "bonus", label: "Bonus", hint: "Extra goodwill, not tied to one booking." },
  { value: "deduction", label: "Deduction", hint: "Something taken off what the captain is owed." },
];

export function AdjustmentModal({ captain, open, onClose }: { captain: Pick<PayrollCaptainDetail, "id" | "full_name">; open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} title={`Add Adjustment — ${captain.full_name}`}>
      <AdjustmentForm captainId={captain.id} onClose={onClose} />
    </Modal>
  );
}

function AdjustmentForm({ captainId, onClose }: { captainId: string; onClose: () => void }) {
  const write = usePayrollWrite();
  const [key] = useState(newIdempotencyKey);
  const [kind, setKind] = useState<AdjustmentInput["kind"]>("allowance");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const amountPaise = toPaise(amount) || 0;
  const mutation = useMutation({
    mutationFn: () => payrollApi.adjust(captainId, { kind, amount_paise: amountPaise, note: note.trim(), idempotency_key: key }),
    onSuccess: (res) => {
      write.ok("Adjustment Added", res.message);
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
        if (amountPaise <= 0) return setError("Enter the amount in whole rupees.");
        if (note.trim().length < 3) return setError("A note is required — say what this is for.");
        if (!mutation.isPending) mutation.mutate();
      }}
    >
      <Select label="Type" value={kind} onChange={(e) => setKind(e.target.value as AdjustmentInput["kind"])}>
        {ADJUST_KINDS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </Select>
      <p className="-mt-2 text-xs text-[var(--color-text-secondary)]">{ADJUST_KINDS.find((k) => k.value === kind)?.hint}</p>
      <Input label="Amount (₹)" inputMode="numeric" value={amount} onChange={(e) => setAmount(cleanAmountInput(e.target.value))} autoFocus />
      <Input label="Note" maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Petrol for Tuesday's long trip" />
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
          Add Adjustment
        </Button>
      </div>
    </form>
  );
}

/** Void a payment (a mistake is reversed, never deleted) — a reason is required. */
export function VoidPaymentModal({ ledgerId, label, onClose }: { ledgerId: string | null; label: string; onClose: () => void }) {
  return (
    <Modal open={!!ledgerId} onClose={onClose} title="Void Payment?">
      {ledgerId && <VoidForm ledgerId={ledgerId} label={label} onClose={onClose} />}
    </Modal>
  );
}

function VoidForm({ ledgerId, label, onClose }: { ledgerId: string; label: string; onClose: () => void }) {
  const write = usePayrollWrite();
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const mutation = useMutation({
    mutationFn: () => payrollApi.voidPayment(ledgerId, reason.trim()),
    onSuccess: (res) => {
      write.ok("Payment Voided", res.message);
      onClose();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });
  return (
    <div className="space-y-4">
      <p className="text-sm text-[var(--color-text-secondary)]">
        {label} will be reversed and the captain's dues go back up. The original row stays in the ledger, marked voided.
      </p>
      <Input label="Reason" maxLength={200} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Entered the wrong amount" autoFocus />
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
          Void Payment
        </Button>
      </div>
    </div>
  );
}
