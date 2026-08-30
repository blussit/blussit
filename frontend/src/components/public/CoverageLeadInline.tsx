import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, MapPin } from "lucide-react";
import { coverageLeadPublicApi } from "../../api/catalog";
import { Button, Input } from "../ui";
import { getErrorMessage } from "../../lib/api-client";

/**
 * "We're not in your area yet" capture — shown the moment a visitor's
 * pincode fails the coverage check (and as a standalone strip lower on the
 * landing page). Turns a dead end into expansion intelligence: the details
 * land in the admin-only Coverage requests tab.
 */
export function CoverageLeadInline({
  pincode,
  prefillName = "",
  prefillPhone = "",
  serviceInterest,
  compact = false,
}: {
  pincode: string;
  prefillName?: string;
  prefillPhone?: string;
  serviceInterest?: string;
  compact?: boolean;
}) {
  const [name, setName] = useState(prefillName);
  const [phone, setPhone] = useState(prefillPhone);
  const [area, setArea] = useState("");
  const [pin, setPin] = useState(pincode);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");

  const mutation = useMutation({
    mutationFn: () =>
      coverageLeadPublicApi.capture({
        name: name.trim(),
        phone: phone.trim(),
        pincode: pin.trim(),
        city_area: area.trim() || undefined,
        service_interest: serviceInterest,
      }),
    onSuccess: () => setDone(true),
    onError: (err) => setError(getErrorMessage(err)),
  });

  if (done) {
    return (
      <div className="flex items-start gap-3 rounded-2xl bg-[var(--color-accent-light)] p-5">
        <CheckCircle2 className="mt-0.5 h-6 w-6 shrink-0 text-[var(--color-success)]" />
        <div>
          <p className="font-semibold text-[var(--color-text-primary)]">You're on the list!</p>
          <p className="mt-1 text-sm text-[var(--color-text-secondary)]">
            We'll message you on WhatsApp the moment we launch in {area.trim() || `pincode ${pin}`}.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className={compact ? "" : "rounded-2xl border border-[var(--color-secondary)]/40 bg-[var(--color-secondary-light)]/60 p-5"}>
      {!compact && (
        <div className="mb-4 flex items-start gap-3">
          <MapPin className="mt-0.5 h-5 w-5 shrink-0 text-[var(--color-secondary)]" />
          <div>
            <p className="font-semibold text-[var(--color-text-primary)]">We're not in your area yet — but we're expanding fast</p>
            <p className="mt-0.5 text-sm text-[var(--color-text-secondary)]">
              Leave your details and you'll be the first to know (and get the launch offer) when we arrive.
            </p>
          </div>
        </div>
      )}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Input label="Your name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Full name" />
        <Input label="WhatsApp number" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="10-digit mobile" maxLength={10} />
        <Input label="Pincode" value={pin} onChange={(e) => setPin(e.target.value)} maxLength={10} />
        <Input label="Area / city (optional)" value={area} onChange={(e) => setArea(e.target.value)} placeholder="e.g. Vijay Nagar, Indore" />
      </div>
      {error && <p className="mt-2 text-sm text-[var(--color-error)]">{error}</p>}
      <Button
        className="mt-4 w-full sm:w-auto"
        isLoading={mutation.isPending}
        disabled={name.trim().length < 2 || phone.trim().length < 10 || pin.trim().length < 4}
        onClick={() => {
          setError("");
          mutation.mutate();
        }}
      >
        Notify me when you launch
      </Button>
    </div>
  );
}
