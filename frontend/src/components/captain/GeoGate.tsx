/**
 * Captain · the location check, on the job screen.
 *
 *  - LocationCheckChip: green once the server verified the captain at the
 *    site, blue when a manager approved it, amber when it could not check.
 *  - GeoBlockNotice: shown when the server REFUSED a step (outside the
 *    customer's location, stale or weak GPS, a trail that doesn't add up).
 *    The captain can only ASK a manager to approve — he can never approve
 *    himself. Honest limit: this is evidence from a phone, not proof.
 */
import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, Loader2, MapPin, ShieldAlert } from "lucide-react";
import { bookingApi, type GeoStep } from "../../api/booking";
import { getErrorMessage } from "../../lib/api-client";
import type { Booking } from "../../types";
import { Btn, Pill } from "./ui";

const ORDER: GeoStep[] = ["complete", "start", "arrival", "heading"];

/** The newest step the server has checked, as a status pill. */
export function LocationCheckChip({ car }: { car: Booking }) {
  const checks = car.geo_checks ?? {};
  const step = ORDER.find((s) => checks[s] && s !== "heading");
  if (!step) return null;
  const check = checks[step]!;
  return (
    <span data-testid="captain-location-check" data-status={check.status}>
      {check.status === "verified" ? (
        <Pill tone="green">
          <Check className="h-3.5 w-3.5" /> Location Verified
        </Pill>
      ) : check.status === "override" ? (
        <Pill tone="blue">
          <MapPin className="h-3.5 w-3.5" /> Approved By Manager
        </Pill>
      ) : (
        <Pill tone="amber">
          <ShieldAlert className="h-3.5 w-3.5" /> Location Not Verified
        </Pill>
      )}
    </span>
  );
}

export interface GeoBlock {
  step: GeoStep;
  message: string;
}

export function GeoBlockNotice({ bookingId, block }: { bookingId: string; block: GeoBlock }) {
  const status = useQuery({
    queryKey: ["geo-status", bookingId],
    queryFn: () => bookingApi.geoStatus(bookingId),
    refetchInterval: 8_000,
  });
  const ask = useMutation({
    mutationFn: () => bookingApi.geoRequestApproval(bookingId, block.step),
    onSuccess: () => void status.refetch(),
  });
  const approval = status.data?.waiting_step === block.step ? status.data.approval : undefined;
  const state = ask.isSuccess && (!approval || approval.status === "none") ? "pending" : (approval?.status ?? "none");

  return (
    <div className="mb-2 rounded-[14px] bg-[#FDECEC] px-3.5 py-3 text-sm text-[#B91C1C]" role="alert" data-testid="captain-geo-block">
      <p className="flex items-start gap-2 font-bold">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" /> {block.message}
      </p>
      {state === "approved" && <p className="mt-2 font-semibold text-[#15803D]">Approved — Try The Step Again Now</p>}
      {state === "pending" && (
        <p className="mt-2 flex items-center gap-2 font-semibold text-[#9A6400]">
          <Loader2 className="h-4 w-4 animate-spin" /> Waiting For Your Manager To Approve
        </p>
      )}
      {state === "denied" && <p className="mt-2 font-semibold">Your Manager Declined{approval?.reason ? `: ${approval.reason}` : ""}</p>}
      {(state === "none" || state === "denied" || state === "expired") && (
        <Btn variant="outline" className="mt-2 w-full" loading={ask.isPending} onClick={() => ask.mutate()} data-testid="captain-ask-manager">
          Ask Manager To Approve
        </Btn>
      )}
      {ask.isError && <p className="mt-2 font-medium">{getErrorMessage(ask.error)}</p>}
    </div>
  );
}
