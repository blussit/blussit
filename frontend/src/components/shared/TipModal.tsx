import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { HandCoins } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { getErrorMessage } from "../../lib/api-client";
import { useToast } from "../../context/ToastContext";
import { Button, Input, Modal } from "../ui";
import type { Booking } from "../../types";

/**
 * Manager/admin: add or change the tip a customer gave on a job the manager
 * did. One tip per visit, in whole rupees, added to the job's total and
 * revenue; 0 removes it.
 */
export function TipModal({ booking, currentTip, onClose, onSaved }: { booking: Booking | null; currentTip: number; onClose: () => void; onSaved?: (tip: number) => void }) {
  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const [amount, setAmount] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (booking) {
      setAmount(currentTip > 0 ? String(currentTip) : "");
      setError("");
    }
  }, [booking, currentTip]);

  const tip = Math.round(Number(amount) || 0);
  const save = useMutation({
    mutationFn: () => bookingApi.setTip(booking!.id, tip),
    onSuccess: () => {
      queryClient.invalidateQueries({
        predicate: (q) => typeof q.queryKey[0] === "string" && /^(center-bookings|admin-bookings|booking|manager-kpi|manager-dashboard|collections|customer-360)/.test(q.queryKey[0]),
      });
      pushToast({ tone: "success", title: tip > 0 ? `Tip of ₹${tip} saved` : "Tip removed" });
      onSaved?.(tip);
      onClose();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

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
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <div className="flex gap-2">
            <Button variant="outline" className="flex-1" onClick={onClose}>
              Cancel
            </Button>
            <Button className="flex-1" isLoading={save.isPending} disabled={tip === currentTip} onClick={() => save.mutate()}>
              <HandCoins className="h-4 w-4" /> Save Tip
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
