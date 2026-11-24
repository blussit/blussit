/**
 * Captain · the location chip on the job screen.
 *
 * LOCATION NEVER BLOCKS THE CAPTAIN: there is no refusal screen and no "ask
 * your manager to approve" any more — a step is always accepted, and a
 * location the server could not verify is the MANAGER's red warning, not the
 * captain's problem. The captain only ever sees a calm green tick when his
 * position was checked at the site; nothing alarming otherwise.
 */
import { Check } from "lucide-react";
import type { Booking } from "../../types";
import { Pill } from "./ui";

const ORDER = ["complete", "start", "arrival"] as const;

/** Green "Location Verified" once the server checked the newest step at the site; nothing otherwise. */
export function LocationCheckChip({ car }: { car: Booking }) {
  const checks = car.geo_checks ?? {};
  const step = ORDER.find((s) => checks[s]);
  if (!step || checks[step]!.status !== "verified") return null;
  return (
    <span data-testid="captain-location-check" data-status="verified">
      <Pill tone="green">
        <Check className="h-3.5 w-3.5" /> Location Verified
      </Pill>
    </span>
  );
}
