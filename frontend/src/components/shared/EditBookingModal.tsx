import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button, Input, Modal } from "../ui";
import { bookingApi } from "../../api/booking";
import { getErrorMessage } from "../../lib/api-client";
import type { Booking } from "../../types";

/**
 * Notes / alternate contact only — never price, capacity, service, vehicle
 * or time (those drive pricing/capacity and need a repricing step this
 * doesn't do; see BookingUpdateDetailsRequest's own docstring). Shared by
 * the manager's own queue and the admin's centerIdOverride-wrapped one.
 * Only ever rendered for a booking that isn't completed/cancelled — the
 * caller decides that, same as every other conditional action here.
 */
export function EditBookingModal({
  booking,
  onClose,
  onSaved,
}: {
  booking: Booking | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [notes, setNotes] = useState("");
  const [altName, setAltName] = useState("");
  const [altPhone, setAltPhone] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (booking) {
      setNotes(booking.customer_notes || "");
      setAltName(booking.alternate_contact_name || "");
      setAltPhone(booking.alternate_contact_phone || "");
      setError("");
    }
  }, [booking]);

  const mutation = useMutation({
    mutationFn: () =>
      bookingApi.updateDetails(booking!.id, {
        customer_notes: notes,
        alternate_contact_name: altName,
        alternate_contact_phone: altPhone,
      }),
    onSuccess: () => {
      onSaved();
      onClose();
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  return (
    <Modal open={!!booking} onClose={onClose} title={`Edit ${booking?.booking_number || "booking"}`}>
      <div className="space-y-4">
        {booking?.booking_group_id && (
          <p className="-mt-1 text-xs text-gray-400">This visit has more than one vehicle — the notes and contact below apply to all of them.</p>
        )}
        <Input label="Customer notes" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Anything the captain should know" />
        <Input label="Alternate contact name" value={altName} onChange={(e) => setAltName(e.target.value)} />
        <Input label="Alternate contact phone" value={altPhone} onChange={(e) => setAltPhone(e.target.value)} placeholder="10-digit mobile" />
        {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
        <Button className="w-full" isLoading={mutation.isPending} onClick={() => mutation.mutate()}>
          Save changes
        </Button>
      </div>
    </Modal>
  );
}
