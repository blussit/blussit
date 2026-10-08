import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { HandCoins } from "lucide-react";
import { staffBookingApi, TIP_METHOD_LABELS, type TipMethod } from "../../api/staffBookings";
import { getErrorMessage } from "../../lib/api-client";
import { useToast } from "../../context/ToastContext";
import { Button, Input, Modal } from "../ui";
import type { Booking } from "../../types";

/** Cash / Online pair for a tip — the tip's own method (MONEY-2),
 *  independent of how the job was paid. 44px taps. */
export function TipMethodToggle({ value, onChange, className = "" }: { value: TipMethod; onChange: (m: TipMethod) => void; className?: string }) {
  return (
    <div className={`grid grid-cols-2 gap-2 ${className}`} role="radiogroup" aria-label="How Was The Tip Given?">
      {(["cash", "online"] as const).map((m) => (
        <button
          key={m}
          type="button"
          role="radio"
          aria-checked={value === m}
          onClick={() => onChange(m)}
          className={`min-h-11 rounded-xl border px-3 text-sm font-medium transition-colors ${
            value === m ? "border-black bg-[var(--color-primary-light)] text-black" : "border-gray-200 text-gray-600 hover:border-gray-400"
          }`}
        >
          {TIP_METHOD_LABELS[m]}
        </button>
      ))}
    </div>
  );
}

/**
 * Manager/admin: add or change the tip a customer gave on a job the manager
 * did. One tip per visit, in whole rupees, added to the job's total and
 * revenue; 0 removes it. The tip carries its own method (cash / online).
 */
export function TipModal({
  booking,
  currentTip,
  currentMethod,
  onClose,
  onSaved,
}: {
  booking: Booking | null;
  currentTip: number;
  /** The saved tip's method (a tip without one is cash). */
  currentMethod?: TipMethod | null;
  onClose: () => void;
  onSaved?: (tip: number, method: TipMethod) => void;
}) {
  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState<TipMethod>("cash");
  const [error, setError] = useState("");
  const savedMethod: TipMethod = currentMethod === "online" ? "online" : "cash";
  useEffect(() => {
    if (booking) {
      setAmount(currentTip > 0 ? String(currentTip) : "");
      setMethod(savedMethod);
      setError("");
    }
  }, [booking, currentTip, savedMethod]);

  const tip = Math.round(Number(amount) || 0);
  const save = useMutation({
    mutationFn: () => staffBookingApi.setTip(booking!.id, tip, method),
    onSuccess: () => {
      queryClient.invalidateQueries({
        predicate: (q) =>
          typeof q.queryKey[0] === "string" && /^(center-bookings|admin-bookings|booking|staff-booking|manager-kpi|manager-dashboard|collections|customer-360)/.test(q.queryKey[0]),
      });
      pushToast({ tone: "success", title: tip > 0 ? `Tip Of ₹${tip} Saved (${TIP_METHOD_LABELS[method]})` : "Tip Removed" });
      onSaved?.(tip, method);
      onClose();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const unchanged = tip === currentTip && (tip === 0 || method === savedMethod);
  return (
    <Modal open={!!booking} onClose={onClose} title={currentTip > 0 ? "Edit Tip" : "Add Tip"} maxWidth="max-w-sm">
      {booking && (
        <div className="space-y-4">
          <p className="text-sm text-[var(--color-text-secondary)]">
            The tip the customer gave for <span className="font-mono-num font-semibold text-[#0E1A33]">{booking.booking_number}</span>
            {booking.booking_group_id ? " (the whole visit)" : ""}. It&apos;s added to the job&apos;s total and your revenue.
          </p>
          <Input
            label="Tip Amount (₹)"
            inputMode="numeric"
            autoFocus
            value={amount}
            onChange={(e) => setAmount(e.target.value.replace(/\D/g, "").slice(0, 6))}
            placeholder="0"
            hint={currentTip > 0 ? "Set 0 to remove the tip." : undefined}
          />
          {tip > 0 && (
            <div>
              <p className="mb-1.5 text-sm font-medium text-[#0E1A33]">Tip Given In</p>
              <TipMethodToggle value={method} onChange={setMethod} />
            </div>
          )}
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <div className="flex gap-2">
            <Button variant="outline" className="min-h-11 flex-1" onClick={onClose}>
              Cancel
            </Button>
            <Button className="min-h-11 flex-1" isLoading={save.isPending} disabled={unchanged} onClick={() => save.mutate()}>
              <HandCoins className="h-4 w-4" /> Save Tip
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
