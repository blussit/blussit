/**
 * Manager / admin · a job's location verdict. Shows each step's server-side
 * check (verified at site / confirmed by a manager / not verified, with the
 * reasons) and a faked-looking trail. Location never blocks the captain, so
 * there is nothing to approve here — the red warning at the top of the
 * booking carries the "Ignore" button.
 */
import { MapPin } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { bookingApi, type GeoStep } from "../../api/booking";
import { Badge } from "../ui";
import type { Booking } from "../../types";

const STEP_LABEL: Record<GeoStep, string> = { heading: "Heading Out", arrival: "Arrived", start: "Service Started", complete: "Service Completed" };
const REASON_LABEL: Record<string, string> = {
  outside_geofence: "Outside The Customer's Location",
  no_fix: "No Location Shared",
  stale_fix: "Old Location",
  future_fix: "Phone Clock Ahead",
  poor_accuracy: "Weak GPS Signal",
  replayed_fix: "Location Reused",
  trail_teleport: "Location Jumped",
  trail_frozen: "Same Spot While Moving",
  trail_accuracy_constant: "GPS Accuracy Never Changes",
  trail_missing: "No Trip Trail",
  too_quick: "Finished Too Fast",
};
const reasons = (list?: string[]) => (list ?? []).map((r) => REASON_LABEL[r] ?? r.replace(/_/g, " "));

export function GeoReview({ car }: { car: Booking }) {
  const status = useQuery({
    queryKey: ["geo-status", car.id],
    queryFn: () => bookingApi.geoStatus(car.id),
    refetchInterval: 15_000,
    enabled: !!car.captain_id,
  });

  const data = status.data;
  if (!car.captain_id || !data) return null;
  const checks = (Object.keys(STEP_LABEL) as GeoStep[]).filter((s) => data.checks[s]);
  if (!checks.length && !data.suspect && !data.refusals) return null;

  return (
    <div data-testid="geo-review">
      <p className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">
        <MapPin className="h-3.5 w-3.5" /> Location Verdict
      </p>
      <div className="space-y-2 rounded-xl border border-gray-100 p-3">
        {data.suspect && (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-xs font-semibold text-[var(--color-error)]">
            Suspicious Location Trail — {reasons(data.suspect_reasons).join(", ")}
          </p>
        )}
        {checks.map((s) => {
          const c = data.checks[s]!;
          return (
            <div key={s} className="flex flex-wrap items-center justify-between gap-2 text-sm">
              <span className="text-[var(--color-text-secondary)]">{STEP_LABEL[s]}</span>
              <span className="flex flex-wrap items-center gap-1.5">
                {c.status === "verified" && <Badge tone="success">Verified At Site{c.distance_m != null ? ` · ${Math.round(c.distance_m)} m` : ""}</Badge>}
                {c.status === "confirmed" && <Badge tone="info">Confirmed By A Manager</Badge>}
                {c.status === "override" && (
                  <Badge tone="info">Approved By {c.override_by_name || "Manager"}{c.override_reason ? ` — ${c.override_reason}` : ""}</Badge>
                )}
                {(c.status === "unverified" || c.status === "unpinned") && (
                  <Badge tone="warning">{c.status === "unpinned" ? "No Map Pin To Check" : "Not Verified"}</Badge>
                )}
                {c.status === "unverified" && c.distance_m != null && c.allowed_m != null && c.distance_m > c.allowed_m ? (
                  <Badge tone="error">{(c.distance_m / 1000).toFixed(1)} Km Away</Badge>
                ) : null}
                {c.status !== "verified" && c.status !== "confirmed" && c.reasons?.length ? <Badge tone="error">{reasons(c.reasons).join(", ")}</Badge> : null}
              </span>
            </div>
          );
        })}
        {data.refusals > 0 && (
          <p className="text-xs text-[var(--color-text-secondary)]">
            The location check flagged {data.refusals} time{data.refusals === 1 ? "" : "s"} on this job (none of them stopped the captain).
          </p>
        )}
      </div>
    </div>
  );
}
