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
    <Modal open={open} onClose={() => { onClose(); setSent(false); }} title="Request a custom plan">
      {sent ? (
        <div className="space-y-4">
          <p className="text-sm text-gray-700">
            Thanks — we have your details and we'll call you to work out a plan that fits.
          </p>
          <Button className="w-full" onClick={() => { onClose(); setSent(false); }}>
            Done
          </Button>
        </div>
      ) : (
        <div className="space-y-4">
          <p className="text-sm text-gray-600">Tell us what you need and we'll price it for you.</p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Input label="Your name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
            <Input label="Phone" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} required />
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Input
              label="How many cars?"
              type="number"
              min={1}
              value={form.vehicle_count}
              onChange={(e) => setForm({ ...form, vehicle_count: Math.max(1, Number(e.target.value) || 1) })}
            />
            <Input
              label="Washes per month (optional)"
              type="number"
              min={1}
              value={form.washes_per_month ?? ""}
              onChange={(e) => setForm({ ...form, washes_per_month: Number(e.target.value) || undefined })}
            />
          </div>
          <Input
            label="Which services?"
            placeholder="e.g. Jet Wash for 3 cars, Star Wash for the SUV"
            value={form.services_wanted}
            onChange={(e) => setForm({ ...form, services_wanted: e.target.value })}
            required
          />
          <Input
            label="Preferred time (optional)"
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
