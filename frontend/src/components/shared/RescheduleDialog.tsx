import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CalendarClock } from "lucide-react";
import { bookingApi } from "../../api/booking";
import { getErrorMessage } from "../../lib/api-client";
import { useToast } from "../../context/ToastContext";
import { formatSlot } from "../../lib/date";
import { Button, Modal } from "../ui";
import { SlotPicker } from "./SlotPicker";
import type { Booking } from "../../types";

const REFRESH = /^(center-bookings|admin-center-bookings|admin-bookings|booking|staff-booking|manager-dashboard)/;

/**
 * Manager / admin: move a booking that hasn't started to another date and
 * slot. The same rules as everywhere (the slot's capacity, holidays, working
 * hours, the booking window, a pass's last booking day) are enforced by the
 * server; the money does not change; the captain (if any) and the customer
 * are told. A confirm line shows the move before it is made.
 */
export function RescheduleDialog({ booking, open, onClose }: { booking: Booking; open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const { push: pushToast } = useToast();
  const [date, setDate] = useState("");
  const [slot, setSlot] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (open) {
      setDate("");
      setSlot("");
      setError("");
    }
  }, [open, booking.id]);
  const move = useMutation({
    mutationFn: () => bookingApi.reschedule(booking.id, date, slot),
    onSuccess: () => {
      queryClient.invalidateQueries({ predicate: (q) => typeof q.queryKey[0] === "string" && REFRESH.test(q.queryKey[0]) });
      queryClient.invalidateQueries({ queryKey: ["booking-group"] });
      pushToast({ tone: "success", title: "Booking Rescheduled", message: "The customer and the captain were told. The money is unchanged." });
      onClose();
    },
    onError: (e) => setError(getErrorMessage(e)),
  });
  const was = `${new Date(booking.scheduled_date).toLocaleDateString("en-IN", { day: "numeric", month: "short" })} · ${formatSlot(booking.scheduled_slot)}`;
  return (
    <Modal open={open} onClose={onClose} title="Reschedule Booking" maxWidth="max-w-md">
      <div className="space-y-4">
        <p className="text-sm text-[var(--color-text-secondary)]">
          <span className="font-mono-num font-semibold text-[#0E1A33]">{booking.booking_number}</span> is now on <span className="font-semibold">{was}</span>. Moving it clears the
          assigned captain (you assign again afterwards), tells the customer, and leaves the amount unchanged.
        </p>
        <SlotPicker serviceCenterId={booking.service_center_id} date={date} onDateChange={setDate} value={slot} onChange={setSlot} />
        {date && slot && (
          <p className="rounded-xl border border-[#F3E5B5] px-3 py-2 text-sm" data-testid="reschedule-confirm">
            {was} → <span className="font-semibold">{new Date(`${date}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short" })} · {formatSlot(slot)}</span>
          </p>
        )}
        {error && (
          <p role="alert" className="text-sm text-[var(--color-error)]">
            {error}
          </p>
        )}
        <div className="flex gap-2">
          <Button variant="outline" className="min-h-11 flex-1" onClick={onClose}>
            Cancel
          </Button>
          <Button className="min-h-11 flex-1" disabled={!date || !slot} isLoading={move.isPending} onClick={() => move.mutate()}>
            <CalendarClock className="h-4 w-4" /> Confirm Reschedule
          </Button>
        </div>
      </div>
    </Modal>
  );
}
