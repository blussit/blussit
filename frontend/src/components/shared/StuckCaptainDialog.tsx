import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { bookingApi } from "../../api/booking";
import { moneyOf, type StaffBooking } from "../../api/staffBookings";
import { getErrorMessage } from "../../lib/api-client";
import { useToast } from "../../context/ToastContext";
import { Button, Modal } from "../ui";

const REFRESH = /^(center-bookings|admin-center-bookings|admin-bookings|booking|staff-booking|booking-group|manager-kpi|manager-dashboard|collections|center-captains)/;

const rupees = (n: number) => `₹${(Number(n) || 0).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;

type Payment = "cash" | "online" | "captain";
const PAYMENT_CHOICES: { value: Payment; label: string; hint: string }[] = [
  { value: "cash", label: "I Collected Cash", hint: "You took the cash — it is recorded as yours, not the captain's." },
  { value: "online", label: "Paid Online", hint: "The customer already paid you online outside the app." },
  { value: "captain", label: "Captain Will Collect", hint: "He takes it from his app (cash or QR) after the job is closed." },
];

/**
 * Manager / admin acting for a captain who is stuck on a job:
 *  - Mark Arrived  — he is at the door but the arrival check won't pass
 *    (locked code, lost message, no signal). He goes on with the photos from
 *    his own app.
 *  - Complete For Captain — he finished the wash but his app can't (no
 *    signal, dead phone). The job closes crediting HIM; no photos needed.
 * A reason is always required (>= 5 characters): it is written to the booking
 * history and the audit log.
 */
export function StuckCaptainDialog({
  mode,
  car,
  open,
  onClose,
}: {
  mode: "arrived" | "complete";
  car: StaffBooking;
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const { push } = useToast();
  const [reason, setReason] = useState("");
  const [payment, setPayment] = useState<Payment | null>(null);
  const due = moneyOf(car).due;
  const needsPayment = mode === "complete" && due > 0.004;

  useEffect(() => {
    if (!open) return;
    setReason("");
    setPayment(null);
  }, [open, car.id, mode]);

  const run = useMutation({
    mutationFn: () => (mode === "arrived" ? bookingApi.markArrived(car.id, reason.trim()) : bookingApi.completeForCaptain(car.id, reason.trim(), payment ?? undefined)),
    onSuccess: () => {
      void queryClient.invalidateQueries({ predicate: (q) => typeof q.queryKey[0] === "string" && REFRESH.test(q.queryKey[0]) });
      push(
        mode === "arrived"
          ? { tone: "success", title: "Arrival Confirmed", message: "The captain's app moves on to the before photo." }
          : { tone: "success", title: "Job Completed For The Captain", message: "He stays the earner for this job." },
      );
      onClose();
    },
    onError: (err) => push({ tone: "error", title: mode === "arrived" ? "Couldn't Confirm Arrival" : "Couldn't Complete The Job", message: getErrorMessage(err) }),
  });

  const ready = reason.trim().length >= 5 && (!needsPayment || payment !== null);
  return (
    <Modal open={open} onClose={onClose} title={mode === "arrived" ? "Mark Arrived" : "Complete For Captain"} maxWidth="max-w-md">
      <div className="space-y-4" data-testid={`stuck-captain-${mode}`}>
        <p className="text-sm text-[var(--color-text-secondary)]">
          {mode === "arrived"
            ? "Confirm that the captain has reached the customer without the service code. He continues with the before photo from his own app."
            : "Close this job for the captain. He stays the earner — his pay, commission and wallet are posted exactly as if he had finished it himself. No photos are needed."}
        </p>
        <label className="block">
          <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Reason</span>
          <textarea
            rows={2}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Why are you doing this? (saved to the history and audit log)"
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
            maxLength={300}
          />
        </label>
        {needsPayment && (
          <div>
            <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">
              {rupees(due)} Is Still Due — How Was It Collected?
            </span>
            <div className="space-y-2">
              {PAYMENT_CHOICES.map((c) => (
                <button
                  key={c.value}
                  type="button"
                  onClick={() => setPayment(c.value)}
                  aria-pressed={payment === c.value}
                  className={`w-full rounded-lg border px-3 py-2 text-left text-sm ${payment === c.value ? "border-black bg-[var(--color-primary-light)]" : "border-gray-200"}`}
                >
                  <span className="block font-semibold text-[var(--color-text-primary)]">{c.label}</span>
                  <span className="block text-xs text-[var(--color-text-secondary)]">{c.hint}</span>
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button isLoading={run.isPending} disabled={!ready} onClick={() => run.mutate()}>
            {mode === "arrived" ? "Confirm Arrival" : "Complete Job"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
