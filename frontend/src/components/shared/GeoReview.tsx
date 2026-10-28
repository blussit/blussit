/**
 * Manager / admin · a job's location verdict and the approval queue.
 * Shows each step's server-side check (verified at site / approved by
 * <manager> / not verified), a faked-looking trail with its reasons, and —
 * when the captain asked — Approve / Decline for ONE step of THIS job (a
 * reason is required and goes to the audit log).
 */
import { useState } from "react";
import { MapPin } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { bookingApi, type GeoStep } from "../../api/booking";
import { getErrorMessage } from "../../lib/api-client";
import { useToast } from "../../context/ToastContext";
import { Badge, Button } from "../ui";
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
  const queryClient = useQueryClient();
  const { push } = useToast();
  const [reason, setReason] = useState("");
  const status = useQuery({
    queryKey: ["geo-status", car.id],
    queryFn: () => bookingApi.geoStatus(car.id),
    refetchInterval: 15_000,
    enabled: !!car.captain_id,
  });
  const decide = useMutation({
    mutationFn: ({ step, approve }: { step: GeoStep; approve: boolean }) => bookingApi.geoOverride(car.id, step, approve, reason.trim()),
    onSuccess: (_d, v) => {
      setReason("");
      void queryClient.invalidateQueries({ queryKey: ["geo-status", car.id] });
      push({ tone: "success", title: v.approve ? "Location Approved" : "Location Declined", message: v.approve ? "The captain can retry this step once." : "The captain was told." });
    },
    onError: (err) => push({ tone: "error", title: "Couldn't Save", message: getErrorMessage(err) }),
  });

  const data = status.data;
  if (!car.captain_id || !data) return null;
  const checks = (Object.keys(STEP_LABEL) as GeoStep[]).filter((s) => data.checks[s]);
  const waiting = data.waiting_step;
  const pending = waiting && data.approval.status === "pending";
  if (!checks.length && !data.suspect && !pending && !data.refusals) return null;

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
              {c.status === "override" && (
                <Badge tone="info">Approved By {c.override_by_name || "Manager"}{c.override_reason ? ` — ${c.override_reason}` : ""}</Badge>
              )}
              {(c.status === "unverified" || c.status === "unpinned") && (
                <Badge tone="warning">{c.status === "unpinned" ? "No Map Pin To Check" : "Not Verified"}</Badge>
              )}
              {c.status !== "verified" && c.reasons?.length ? <Badge tone="error">{reasons(c.reasons).join(", ")}</Badge> : null}
            </span>
          </div>
        );
      })}
      {data.refusals > 0 && <p className="text-xs text-[var(--color-text-secondary)]">Refused {data.refusals} time{data.refusals === 1 ? "" : "s"} by the location check.</p>}
      {waiting && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
          <p className="text-sm font-semibold text-amber-800">
            {pending ? `The Captain Asked To ${STEP_LABEL[waiting]}` : `Approve ${STEP_LABEL[waiting]} Without A Location Match`}
          </p>
          <textarea
            rows={2}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Reason (required, saved to the audit log)"
            className="mt-2 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
          />
          <div className="mt-2 flex gap-2">
            <Button size="sm" isLoading={decide.isPending} disabled={reason.trim().length < 5} onClick={() => decide.mutate({ step: waiting, approve: true })}>
              Approve This Step
            </Button>
            {pending && (
              <Button size="sm" variant="outline" disabled={reason.trim().length < 5 || decide.isPending} onClick={() => decide.mutate({ step: waiting, approve: false })}>
                Decline
              </Button>
            )}
          </div>
          <p className="mt-1.5 text-xs text-amber-800">Covers this one step of this job, once, for 30 minutes.</p>
        </div>
      )}
      </div>
    </div>
  );
}
