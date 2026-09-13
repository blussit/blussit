import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Button, Input, Modal } from "../ui";
import { subscriptionApi, type PlanEnquiryPayload } from "../../api/engagement";
import { getErrorMessage } from "../../lib/api-client";

/** "None of these fit us" — captured as a lead the team calls back. */
export function CustomPlanEnquiryModal({
  open,
  onClose,
  defaultName,
  defaultPhone,
}: {
  open: boolean;
  onClose: () => void;
  defaultName?: string | null;
  defaultPhone?: string | null;
}) {
  const [form, setForm] = useState<PlanEnquiryPayload>({
    name: defaultName || "",
    phone: defaultPhone || "",
    vehicle_count: 1,
    services_wanted: "",
    washes_per_month: undefined,
    preferred_time: "",
    notes: "",
  });
  const [error, setError] = useState("");
  const [sent, setSent] = useState(false);
  // A plain number input fighting `Math.max(1, ...)` on every keystroke
  // means clearing "1" to type "2" snaps straight back to "1" before the
  // "2" ever lands — the field LOOKS stuck. Free-typing here (including a
  // momentarily empty box) and only clamping on blur fixes that.
  const [vehicleCountText, setVehicleCountText] = useState(String(form.vehicle_count));

  const mutation = useMutation({
    mutationFn: () =>
      subscriptionApi.submitEnquiry({
        ...form,
        preferred_time: form.preferred_time || undefined,
        notes: form.notes || undefined,
        washes_per_month: form.washes_per_month || undefined,
      }),
    onSuccess: () => {
      setSent(true);
      setError("");
    },
    onError: (err) => setError(getErrorMessage(err)),
  });

  const valid = form.name.trim().length >= 2 && form.phone.trim().length >= 10 && form.services_wanted.trim().length >= 2;

  return (
    <Modal open={open} onClose={() => { onClose(); setSent(false); }} title="Request A Custom Plan">
      {sent ? (
        <div className="space-y-4">
          <p className="text-sm text-gray-700">
            Thanks. We Have Your Details And We'll Call You To Work Out A Plan That Fits.
          </p>
          <Button className="w-full" onClick={() => { onClose(); setSent(false); }}>
            Done
          </Button>
        </div>
      ) : (
        <div className="space-y-4">
          <p className="text-sm text-gray-600">Tell Us What You Need And We'll Price It For You.</p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Input label="Your Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
            <Input label="Phone" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} required />
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Input
              label="How Many Cars?"
              type="number"
              min={1}
              value={vehicleCountText}
              onChange={(e) => {
                const raw = e.target.value;
                setVehicleCountText(raw);
                const n = Number(raw);
                if (raw !== "" && Number.isFinite(n) && n >= 1) setForm({ ...form, vehicle_count: n });
              }}
              onBlur={() => {
                const n = Math.max(1, Number(vehicleCountText) || 1);
                setVehicleCountText(String(n));
                setForm({ ...form, vehicle_count: n });
              }}
            />
            <Input
              label="Washes Per Month (Optional)"
              type="number"
              min={1}
              value={form.washes_per_month ?? ""}
              onChange={(e) => setForm({ ...form, washes_per_month: Number(e.target.value) || undefined })}
            />
          </div>
          <Input
            label="Which Services?"
            placeholder="e.g. Jet Wash for 3 cars, Star Wash for the SUV"
            value={form.services_wanted}
            onChange={(e) => setForm({ ...form, services_wanted: e.target.value })}
            required
          />
          <Input
            label="Preferred Time (Optional)"
            placeholder="e.g. every Saturday morning"
            value={form.preferred_time}
            onChange={(e) => setForm({ ...form, preferred_time: e.target.value })}
          />
          <Input
            label="Anything else (optional)"
            value={form.notes}
            onChange={(e) => setForm({ ...form, notes: e.target.value })}
          />
          {error && <p className="text-sm text-[var(--color-error)]">{error}</p>}
          <Button className="w-full" disabled={!valid} isLoading={mutation.isPending} onClick={() => mutation.mutate()}>
            Send request
          </Button>
        </div>
      )}
    </Modal>
  );
}
