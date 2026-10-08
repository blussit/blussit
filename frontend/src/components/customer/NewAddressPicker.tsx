/**
 * "Add New Address" inside the customer sheets (Edit Booking, Book A Plan
 * Wash) — the booking page's own address block: the shared LocationPicker
 * (map pin + area search + Use My Location), and, when Maps can't load or
 * the customer prefers, the same typed Address + Pincode fallback.
 *
 * The value is the QuickAddress the server takes (POST /bookings/quick
 * `address`, PATCH /bookings/{id} `address`): the pin's label as line1,
 * its city/state/pincode, and the pin itself. A typed address has no pin.
 *
 * `useAddressCoverage` is the coverage check the sheets run for whichever
 * address is picked (saved or new) — the same POST the booking page makes.
 */
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BadgeCheck } from "lucide-react";
import { coverageApi } from "../../api/catalog";
import type { BookingEditAddress } from "../../api/bookingEdit";
import { LocationPicker, type LocationValue } from "../shared/LocationPicker";
import { Input, Spinner } from "../ui";

export type NewAddress = BookingEditAddress;

const pinLabel = (v: LocationValue) => v.formatted || [v.area, v.city].filter(Boolean).join(", ") || "Pinned Location";

export function NewAddressPicker({ onChange }: { onChange: (value: NewAddress | null) => void }) {
  const [pinned, setPinned] = useState<LocationValue | null>(null);
  const [mapsUp, setMapsUp] = useState(true);
  const [typed, setTyped] = useState(false);
  const [line1, setLine1] = useState("");
  const [pincode, setPincode] = useState("");
  const usingPin = mapsUp && !typed;

  const value: NewAddress | null = usingPin
    ? pinned
      ? {
          line1: pinLabel(pinned),
          city: pinned.city || undefined,
          state: pinned.state || undefined,
          pincode: pinned.pincode || undefined,
          latitude: pinned.latitude,
          longitude: pinned.longitude,
        }
      : null
    : line1.trim().length >= 3 && /^\d{6}$/.test(pincode)
      ? { line1: line1.trim(), pincode, latitude: null, longitude: null }
      : null;
  const key = JSON.stringify(value);
  const sent = useRef<string | null>(null);
  useEffect(() => {
    if (sent.current === key) return;
    sent.current = key;
    onChange(value);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return (
    <div className="space-y-3" data-testid="new-address">
      {usingPin && <LocationPicker value={pinned} onUnavailable={() => setMapsUp(false)} onChange={setPinned} height="13rem" />}
      {mapsUp && (
        <button
          type="button"
          onClick={() => {
            setTyped((v) => !v);
            setPinned(null);
          }}
          className="min-h-[44px] text-xs font-semibold text-[#5F6878] underline underline-offset-2 hover:text-[#0A66F0]"
        >
          {typed ? "Pin On The Map Instead" : "Type The Address Instead"}
        </button>
      )}
      {!usingPin && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_150px]">
          <Input
            id="new-address-line1"
            label="Address"
            value={line1}
            maxLength={300}
            onChange={(e) => setLine1(e.target.value)}
            placeholder="House / flat, street, area"
            hint={!mapsUp ? "Maps are unavailable — type the address." : undefined}
          />
          <Input
            id="new-address-pincode"
            label="Pincode"
            value={pincode}
            maxLength={6}
            inputMode="numeric"
            placeholder="452001"
            onChange={(e) => setPincode(e.target.value.replace(/\D/g, ""))}
          />
        </div>
      )}
    </div>
  );
}

/** Where to check coverage for: a saved or a new address. */
export interface CoverageProbe {
  latitude?: number | null;
  longitude?: number | null;
  pincode?: string | null;
}

export function useAddressCoverage(probe: CoverageProbe | null, enabled = true) {
  const lat = probe?.latitude ?? undefined;
  const lng = probe?.longitude ?? undefined;
  const pin = probe?.pincode || undefined;
  return useQuery({
    queryKey: ["address-coverage", lat ?? null, lng ?? null, pin ?? null],
    queryFn: () =>
      coverageApi.check({ latitude: lat, longitude: lng, pincode: pin }) as Promise<
        Awaited<ReturnType<typeof coverageApi.check>> & { pin_required?: boolean }
      >,
    enabled: enabled && !!probe && ((lat != null && lng != null) || !!pin),
    staleTime: 10 * 60 * 1000,
    retry: 1,
  });
}

/** The booking page's coverage lines: checking / we come here / not served. */
export function CoverageNote({ coverage, newPin }: { coverage: ReturnType<typeof useAddressCoverage>; newPin?: boolean }) {
  if (coverage.fetchStatus === "fetching" && !coverage.data) {
    return (
      <p className="flex items-center gap-2 text-xs text-[#5F6878]">
        <Spinner className="h-3.5 w-3.5" /> Checking the area…
      </p>
    );
  }
  if (coverage.isError) return <p className="text-xs font-semibold text-[#C62828]">Couldn't check this address. Try again.</p>;
  if (!coverage.data) return null;
  if (!coverage.data.covered) {
    return (
      <p className="text-xs font-semibold text-[#C62828]">
        {coverage.data.pin_required
          ? "Please pin your exact location on the map — we can't confirm the area from a pincode alone."
          : "We don't serve this address yet — pick another."}
      </p>
    );
  }
  return newPin ? (
    <p className="flex items-start gap-1.5 text-xs text-[#12804A]">
      <BadgeCheck className="mt-px h-3.5 w-3.5 shrink-0" />
      <span className="font-semibold">We come here.</span>
    </p>
  ) : null;
}
