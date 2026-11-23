import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { PackageCheck, PackageX } from "lucide-react";
import { Button, Input, Modal, Select } from "../ui";
import { useConfirm } from "../../context/ConfirmContext";
import { newIdempotencyKey, payrollApi, type PaymentMethod, type PayrollCaptainDetail } from "../../api/payroll";
import { getErrorMessage } from "../../lib/api-client";
import { format, todayIST } from "../../lib/date";
import { cleanAmountInput, rupeesInput, rupeesText, toPaise } from "../../lib/paise";
import { ChoicePair, KitItemsEditor, kitDraftFromItems, kitItemsFromDraft, type KitItemDraft } from "./PayFields";
import { Banner, Fact, FRANCHISE_BANNER } from "./payrollUi";
import { usePayrollWrite } from "./usePayrollWrite";

type DepositAction = "receive" | "refund" | "forfeit" | "expect";

const ACTION_COPY: Record<DepositAction, { title: string; cta: string; hint: string }> = {
  receive: { title: "Receive Deposit", cta: "Record Deposit", hint: "Money the captain handed over as a deposit." },
  refund: { title: "Refund Deposit", cta: "Refund Deposit", hint: "Money given back to the captain, for example when they leave." },
  forfeit: { title: "Forfeit Deposit", cta: "Forfeit Deposit", hint: "Keep the deposit, for example for a kit that was not returned." },
  expect: { title: "Change Expected Deposit", cta: "Save Expected Deposit", hint: "What this captain is meant to deposit. Nothing is received or paid by changing it." },
};

/** Deposit (expected vs received, receive / refund / forfeit) and the equipment kit. */
export function DepositKitTab({ detail }: { detail: PayrollCaptainDetail }) {
  const [action, setAction] = useState<DepositAction | null>(null);
  const expected = detail.pay.deposit_expected_paise || 0;
  const held = detail.deposit_held_paise || 0;
  const pending = Math.max(0, expected - held);

  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Deposit</h3>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Fact label="Expected">
            <span className="font-mono-num">{rupeesText(expected)}</span>
          </Fact>
          <Fact label="Held Now">
            <span className="font-mono-num">{rupeesText(held)}</span>
          </Fact>
          <Fact label="Still To Receive">
            <span className="font-mono-num">{rupeesText(pending)}</span>
          </Fact>
        </div>
        {!detail.finance_impact && <Banner>{FRANCHISE_BANNER}</Banner>}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => setAction("receive")}>
            Receive Deposit
          </Button>
          <Button size="sm" variant="outline" disabled={held <= 0} onClick={() => setAction("refund")}>
            Refund
          </Button>
          <Button size="sm" variant="outline" disabled={held <= 0} onClick={() => setAction("forfeit")}>
            Forfeit
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setAction("expect")}>
            Change Expected
          </Button>
        </div>
        <p className="text-xs text-[var(--color-text-secondary)]">A deposit is money you owe back, so it is a liability in Finance — it is not income.</p>
      </section>

      <KitSection detail={detail} />

      <Modal open={!!action} onClose={() => setAction(null)} title={action ? ACTION_COPY[action].title : "Deposit"}>
        {action && <DepositForm detail={detail} action={action} onClose={() => setAction(null)} />}
      </Modal>
    </div>
  );
}

function DepositForm({ detail, action, onClose }: { detail: PayrollCaptainDetail; action: DepositAction; onClose: () => void }) {
  const write = usePayrollWrite();
  const confirm = useConfirm();
  const [key] = useState(newIdempotencyKey);
  const held = detail.deposit_held_paise || 0;
  const expected = detail.pay.deposit_expected_paise || 0;
  const suggested = action === "receive" ? Math.max(0, expected - held) : action === "expect" ? expected : held;
  const [amount, setAmount] = useState(rupeesInput(suggested));
  const [method, setMethod] = useState<PaymentMethod>("cash");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const paise = toPaise(amount) || 0;
  const copy = ACTION_COPY[action];

  const mutation = useMutation({
    mutationFn: () => payrollApi.deposit(detail.id, { action: action === "receive" ? "received" : action, amount_paise: paise, method, note: note.trim() || undefined, idempotency_key: key }),
    onSuccess: (res) => {
      write.ok(`${copy.title} Done`, res.message);
      onClose();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const submit = async () => {
    setError("");
    if (action !== "expect" && paise <= 0) return setError("Enter the amount in whole rupees.");
    if (action === "expect" && toPaise(amount) === null) return setError("Enter the amount in whole rupees (0 for none).");
    if ((action === "refund" || action === "forfeit") && paise > held) return setError(`Only ${rupeesText(held)} is held.`);
    if (action === "forfeit" && note.trim().length < 3) return setError("A reason is required to forfeit a deposit.");
    if (action === "forfeit" && !(await confirm({ title: `Forfeit ${rupeesText(paise)}?`, message: "The captain will not get this money back.", confirmLabel: "Forfeit", tone: "danger" }))) return;
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
      <p className="text-sm text-[var(--color-text-secondary)]">{copy.hint}</p>
      <Input label="Amount (₹)" inputMode="numeric" value={amount} onChange={(e) => setAmount(cleanAmountInput(e.target.value))} autoFocus />
      {(action === "receive" || action === "refund") && (
        <Select label={action === "receive" ? "Received By" : "Paid By"} value={method} onChange={(e) => setMethod(e.target.value as PaymentMethod)}>
          <option value="cash">Cash</option>
          <option value="upi">UPI</option>
          <option value="bank">Bank</option>
          <option value="other">Other</option>
        </Select>
      )}
      {action !== "expect" && <Input label={action === "forfeit" ? "Reason" : "Note (Optional)"} maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} />}
      {error && (
        <p role="alert" className="text-sm text-[var(--color-error)]">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="button" variant="outline" className="flex-1" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" variant={action === "forfeit" ? "danger" : "primary"} className="flex-1" isLoading={mutation.isPending}>
          {copy.cta}
        </Button>
      </div>
    </form>
  );
}

function KitSection({ detail }: { detail: PayrollCaptainDetail }) {
  const write = usePayrollWrite();
  const confirm = useConfirm();
  const kit = detail.pay.kit;
  const [provided, setProvided] = useState<"" | "yes" | "no">(kit?.provided ? "yes" : "no");
  const [items, setItems] = useState<KitItemDraft[]>(kitDraftFromItems(kit?.items));
  const [issued, setIssued] = useState(kit?.issued_on || todayIST());
  const [error, setError] = useState("");

  const save = useMutation({
    mutationFn: () => payrollApi.kit(detail.id, { provided: provided === "yes", items: provided === "yes" ? kitItemsFromDraft(items) : [], issued_on: provided === "yes" ? issued : null }),
    onSuccess: (res) => write.ok("Kit Saved", res.message),
    onError: (err) => setError(getErrorMessage(err)),
  });
  const returnKit = useMutation({
    mutationFn: () => payrollApi.returnKit(detail.id, { returned_on: todayIST() }),
    onSuccess: (res) => write.ok("Kit Marked Returned", res.message),
    onError: (err) => write.fail(err, "Couldn't Mark The Kit Returned"),
  });

  const returned = !!kit?.provided && !!kit.returned_on;
  const withCaptain = !!kit?.provided && !kit.returned_on;

  return (
    <section className="space-y-3">
      <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">Kit</h3>
      {withCaptain && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-amber-200 bg-amber-50/60 px-3 py-2.5 text-sm">
          <span className="inline-flex items-center gap-2 text-amber-800">
            <PackageCheck className="h-4 w-4" /> With the captain{kit?.issued_on ? ` since ${format(kit.issued_on)}` : ""}
          </span>
          <Button
            size="sm"
            variant="outline"
            isLoading={returnKit.isPending}
            onClick={async () => {
              if (await confirm({ title: "Mark Kit Returned?", message: "Use this once every item is back with you.", confirmLabel: "Mark Returned", tone: "default" })) returnKit.mutate();
            }}
          >
            Mark Kit Returned
          </Button>
        </div>
      )}
      {returned && (
        <p className="inline-flex items-center gap-2 rounded-xl border border-green-200 bg-green-50 px-3 py-2.5 text-sm text-green-800">
          <PackageX className="h-4 w-4" /> Returned on {format(kit!.returned_on!)}
        </p>
      )}

      <ChoicePair
        label="Kit Provided"
        value={provided}
        options={[
          { value: "yes", label: "Yes" },
          { value: "no", label: "No" },
        ]}
        onChange={(v) => {
          setProvided(v);
          if (v === "yes" && !items.length) setItems([{ name: "", qty: "1", serial: "", value: "" }]);
        }}
      />
      {provided === "yes" && (
        <>
          <KitItemsEditor items={items} onChange={setItems} />
          <Input type="date" label="Issued On" value={issued} max={todayIST()} onChange={(e) => setIssued(e.target.value)} />
        </>
      )}
      {error && (
        <p role="alert" className="text-sm text-[var(--color-error)]">
          {error}
        </p>
      )}
      <Button
        size="sm"
        isLoading={save.isPending}
        onClick={() => {
          setError("");
          if (provided === "yes" && !kitItemsFromDraft(items).length) return setError("Add at least one kit item, or choose No.");
          save.mutate();
        }}
      >
        Save Kit
      </Button>
    </section>
  );
}
