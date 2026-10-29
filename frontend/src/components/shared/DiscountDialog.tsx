import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { BadgePercent } from "lucide-react";
import { adjustmentApi, newIdempotencyKey, type DiscountResult, type StaffBooking } from "../../api/staffBookings";
import { getErrorCode, getErrorMessage } from "../../lib/api-client";
import { useToast } from "../../context/ToastContext";
import { useConfirm } from "../../context/ConfirmContext";
import { Button, Input, Modal } from "../ui";

const REFRESH = /^(center-bookings|admin-center-bookings|admin-bookings|booking|staff-booking|manager-kpi|manager-dashboard|collections|customer-360)/;

const rupees = (n: number) => `₹${Math.round(n * 100) / 100}`;

/**
 * Manager / admin: give, raise, lower or remove a discount on a booking that
 * already exists. A target amount (or a percent of the bill) with a short
 * reason; a live preview shows the booking's total and amount due before and
 * after, and any money that goes back to the customer's wallet — then a
 * confirm shows the same numbers once more. Retrying is safe (one idempotency
 * key per dialog); a booking that changed meanwhile is refused with a clear line.
 */
export function DiscountDialog({
  cars,
  car,
  open,
  onClose,
}: {
  /** Every live car of the visit (one entry for a single booking). */
  cars: StaffBooking[];
  /** The car the drawer is on. */
  car: StaffBooking;
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const confirm = useConfirm();
  const isVisit = cars.length > 1;
  const [scope, setScope] = useState<"booking" | "visit">("booking");
  const [mode, setMode] = useState<"amount" | "percent">("amount");
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<DiscountResult | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const keyRef = useRef(newIdempotencyKey());

  const scoped = scope === "visit" ? cars : [car];
  const current = scoped.reduce((sum, c) => sum + (Number(c.manager_discount) || 0), 0);
  const total = scoped.reduce((sum, c) => sum + (Number(c.total_amount) || 0), 0);

  useEffect(() => {
    if (!open) return;
    setScope(isVisit ? "visit" : "booking");
    setMode("amount");
    setValue("");
    setReason("");
    setError("");
    setPreview(null);
    keyRef.current = newIdempotencyKey();
  }, [open, car.id, isVisit]);
  // The box starts on what is given today.
  useEffect(() => {
    if (open) setValue(current > 0 ? String(current) : "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, scope]);

  const body = useMemo(() => {
    const n = Number(value);
    if (value.trim() === "" || !Number.isFinite(n) || n < 0) return null;
    if (mode === "amount" && !Number.isInteger(n)) return null;
    if (mode === "percent" && n > 100) return null;
    if (reason.trim().length < 5) return null;
    return { [mode === "amount" ? "amount" : "percent"]: n, reason: reason.trim(), scope: isVisit ? scope : "booking", expected_version: car.version } as const;
  }, [value, mode, reason, scope, isVisit, car.version]);

  // Live preview: the real transaction, aborted (dry_run).
  useEffect(() => {
    if (!open || !body) {
      setPreview(null);
      return;
    }
    let live = true;
    setPreviewing(true);
    const timer = window.setTimeout(() => {
      adjustmentApi
        .discount(car.id, { ...body, dry_run: true })
        .then((r) => live && (setPreview(r), setError("")))
        .catch((e) => live && (setPreview(null), setError(getErrorMessage(e))))
        .finally(() => live && setPreviewing(false));
    }, 450);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [open, body, car.id]);

  const save = useMutation({
    mutationFn: () => adjustmentApi.discount(car.id, { ...body!, idempotency_key: keyRef.current }),
    onSuccess: (r) => {
      queryClient.invalidateQueries({ predicate: (q) => typeof q.queryKey[0] === "string" && REFRESH.test(q.queryKey[0]) });
      queryClient.invalidateQueries({ queryKey: ["booking-group"] });
      queryClient.invalidateQueries({ queryKey: ["booking-adjustments"] });
      pushToast({ tone: "success", title: r.discount_after > 0 ? `Discount Of ${rupees(r.discount_after)} Saved` : "Discount Removed", message: r.message });
      onClose();
    },
    onError: (e) => setError(getErrorCode(e) === "STALE_BOOKING" ? "This booking changed while you were looking at it — close this and open it again." : getErrorMessage(e)),
  });

  const apply = async () => {
    if (!body || !preview) return;
    const ok = await confirm({
      title: preview.discount_after > 0 ? "Apply This Discount?" : "Remove The Discount?",
      message:
        `Discount ${rupees(preview.discount_before)} → ${rupees(preview.discount_after)}. ` +
        `Total ${rupees(total)} → ${rupees(preview.total_after)}. Amount due → ${rupees(preview.amount_due_after)}.` +
        (preview.wallet_credit > 0 ? ` ${rupees(preview.wallet_credit)} goes back to the customer's wallet (money they already paid).` : "") +
        " The customer is told.",
      confirmLabel: "Yes, Save It",
      tone: "default",
    });
    if (ok) save.mutate();
  };

  const unchanged = !!preview && !preview.changed;
  return (
    <Modal open={open} onClose={onClose} title="Give Discount" maxWidth="max-w-md">
      <div className="space-y-4">
        <p className="text-sm text-[var(--color-text-secondary)]">
          For <span className="font-mono-num font-semibold text-[#0E1A33]">{car.booking_number}</span>
          {isVisit && scope === "visit" ? ` and the other ${cars.length - 1} vehicle${cars.length > 2 ? "s" : ""} on this visit` : ""}. Bill now{" "}
          <span className="font-mono-num font-semibold">{rupees(total)}</span>
          {current > 0 && (
            <>
              , discount given <span className="font-mono-num font-semibold">{rupees(current)}</span>
            </>
          )}
          .
        </p>
        {isVisit && (
          <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label="Where To Apply It">
            {(["visit", "booking"] as const).map((s) => (
              <button
                key={s}
                type="button"
                role="radio"
                aria-checked={scope === s}
                onClick={() => setScope(s)}
                className={`min-h-11 rounded-xl border px-3 text-sm font-medium transition-colors ${scope === s ? "border-black bg-[var(--color-primary-light)] text-black" : "border-gray-200 text-gray-600 hover:border-gray-400"}`}
              >
                {s === "visit" ? "Whole Visit" : "This Vehicle Only"}
              </button>
            ))}
          </div>
        )}
        <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label="Discount In">
          {(["amount", "percent"] as const).map((m) => (
            <button
              key={m}
              type="button"
              role="radio"
              aria-checked={mode === m}
              onClick={() => {
                setMode(m);
                setValue("");
              }}
              className={`min-h-11 rounded-xl border px-3 text-sm font-medium transition-colors ${mode === m ? "border-black bg-[var(--color-primary-light)] text-black" : "border-gray-200 text-gray-600 hover:border-gray-400"}`}
            >
              {m === "amount" ? "Rupees (₹)" : "Percent (%)"}
            </button>
          ))}
        </div>
        <Input
          label={mode === "amount" ? "Discount Amount (₹)" : "Discount (% Of The Bill)"}
          inputMode={mode === "amount" ? "numeric" : "decimal"}
          autoFocus
          value={value}
          onChange={(e) => setValue(mode === "amount" ? e.target.value.replace(/\D/g, "").slice(0, 8) : e.target.value.replace(/[^\d.]/g, "").slice(0, 6))}
          placeholder="0"
          hint={current > 0 ? "This is the total discount on the booking — set 0 to remove it." : "Whole rupees; it can't be more than the bill."}
        />
        <Input
          label="Reason"
          value={reason}
          maxLength={300}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Customer waited 40 minutes for the captain"
          hint="A short reason (at least 5 characters) — it stays in the booking's history."
        />
        {preview && body && (
          <div className="rounded-xl border border-[#F3E5B5] bg-white p-3 text-sm" data-testid="discount-preview">
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">After This Change</p>
            <p>
              Total <span className="font-mono-num font-semibold">{rupees(total)}</span> →{" "}
              <span className="font-mono-num font-semibold">{rupees(preview.total_after)}</span>
            </p>
            <p>
              Amount Due <span className="font-mono-num font-semibold">{rupees(preview.amount_due_after)}</span>
            </p>
            {preview.wallet_credit > 0 && (
              <p className="text-[var(--color-text-secondary)]">
                {rupees(preview.wallet_credit)} goes back to the customer&apos;s wallet.
              </p>
            )}
            {unchanged && <p className="text-[var(--color-text-secondary)]">That is already the discount.</p>}
          </div>
        )}
        {previewing && !preview && <p className="text-xs text-[var(--color-text-secondary)]">Checking The Numbers…</p>}
        {error && (
          <p role="alert" className="text-sm text-[var(--color-error)]">
            {error}
          </p>
        )}
        <div className="flex gap-2">
          <Button variant="outline" className="min-h-11 flex-1" onClick={onClose}>
            Cancel
          </Button>
          <Button className="min-h-11 flex-1" isLoading={save.isPending} disabled={!body || !preview || unchanged || previewing} onClick={apply}>
            <BadgePercent className="h-4 w-4" /> Save Discount
          </Button>
        </div>
      </div>
    </Modal>
  );
}
