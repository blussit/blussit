import { AlertTriangle, BadgeCheck, CarFront, CheckCircle2, MapPin, Navigation, Phone, Sparkles, XCircle } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Badge, Button, Card, StatusBadge } from "../ui";
import { format, minutesUntilSlotStart, URGENT_ASSIGNMENT_MINUTES } from "../../lib/date";
import { ISSUE_LABELS } from "../../lib/constants";
import { cn } from "../../lib/cn";
import { vehicleTypeApi } from "../../api/catalog";
import type { Booking } from "../../types";

export interface JobAction {
  label: string;
  kind: "heading" | "verify" | "before" | "after";
}

// Distinct color per stage, so the card's main action reads at a glance
// without having to read the label — "heading" keeps the original dark
// primary color, the rest step through blue → amber → green as the job
// actually progresses toward done.
const ACTION_BUTTON_VARIANT: Record<JobAction["kind"], "primary" | "info" | "secondary" | "success"> = {
  heading: "primary",
  verify: "info",
  before: "secondary",
  after: "success",
};

export function JobCard({
  job,
  action,
  canCancel,
  canReportRisk,
  showEarnings,
  onAction,
  onCancel,
  onReportRisk,
}: {
  job: Booking;
  action: JobAction | null;
  canCancel: boolean;
  canReportRisk: boolean;
  /** The wallet system is off by default (see AdminPricingPage's toggle) and
   * is being managed manually for now — don't show a per-job fee breakdown
   * while that's true, it'd just be confusing next to a hidden wallet. */
  showEarnings: boolean;
  onAction: (kind: JobAction["kind"]) => void;
  onCancel: () => void;
  onReportRisk: () => void;
}) {
  // Cached across every JobCard on the page (same react-query key) — one
  // network fetch, not one per card.
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const vehicleTypeName = (id: string) => vehicleTypes?.find((t) => t.id === id)?.name || id;

  const isFlagged = !!job.issue_flag && !job.issue_resolved;
  const minutesLeft = minutesUntilSlotStart(job.scheduled_date, job.scheduled_slot);
  // Hasn't started heading yet and the slot's coming right up — a
  // heads-up before this would otherwise turn into a captain_not_reached
  // flag. A flagged job is already the more severe state, so this only
  // applies when it isn't flagged yet.
  const isUrgent = job.status === "assigned" && !isFlagged && minutesLeft <= URGENT_ASSIGNMENT_MINUTES;

  return (
    <Card className={cn("p-5", isFlagged && "border-l-4 border-l-amber-500 bg-amber-50/40", isUrgent && "border-l-4 border-l-[var(--color-error)] bg-red-50/40")}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="font-mono-num text-sm font-semibold text-[var(--color-text-primary)]">{job.booking_number}</p>
          <p className="mt-0.5 flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)]">
            <Navigation className="h-3.5 w-3.5" /> {format(job.scheduled_date)} · {job.scheduled_slot}
          </p>
          {isUrgent && (
            <p className="mt-1 flex items-center gap-1.5 text-xs font-semibold text-[var(--color-error)]">
              <AlertTriangle className="h-3.5 w-3.5" />
              {minutesLeft <= 0 ? "Starting now — head out immediately" : `Starts in ${Math.round(minutesLeft)} min — head out now`}
            </p>
          )}
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <StatusBadge status={job.status} />
          {isFlagged && (
            <Badge tone="warning">
              <AlertTriangle className="h-3 w-3" /> {ISSUE_LABELS[job.issue_flag!] || job.issue_flag}
            </Badge>
          )}
        </div>
      </div>

      <div className="mt-4 space-y-2 text-sm">
        {(job.customer_name || job.customer_phone) && (
          <div className="flex items-center gap-2 text-[var(--color-text-primary)]">
            <span className="font-medium">{job.customer_name || "Customer"}</span>
            {job.customer_phone && (
              <a href={`tel:${job.customer_phone}`} className="flex items-center gap-1 text-[var(--color-primary)] hover:underline" onClick={(e) => e.stopPropagation()}>
                <Phone className="h-3.5 w-3.5" /> {job.customer_phone}
              </a>
            )}
          </div>
        )}
        <div className="flex items-start gap-2 text-[var(--color-text-secondary)]">
          <MapPin className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-primary)]" />
          {job.address_snapshot
            ? `${job.address_snapshot.line1}, ${job.address_snapshot.city} - ${job.address_snapshot.pincode}`
            : "Address unavailable"}
        </div>
        <div className="flex items-center gap-2 text-[var(--color-text-secondary)]">
          <CarFront className="h-4 w-4 shrink-0 text-[var(--color-primary)]" />
          {/* Registration number is deliberately NOT shown here — the vehicle-verify
              step exists so the captain types the plate they actually see on the
              car; pre-filling it on the dashboard would let that step be rubber-
              stamped from memory instead of a real check. */}
          {job.vehicle_snapshot
            ? `${job.vehicle_snapshot.brand} ${job.vehicle_snapshot.model} (${vehicleTypeName(job.vehicle_snapshot.vehicle_type)})`
            : "Vehicle details unavailable"}
        </div>
        <div className="flex items-center gap-2 text-[var(--color-text-secondary)]">
          <Sparkles className="h-4 w-4 shrink-0 text-[var(--color-primary)]" />
          {job.combo_name || job.service_names?.join(", ") || "Service details unavailable"}
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Badge tone="neutral">₹{job.total_amount}</Badge>
        {showEarnings && job.captain_earning != null && <Badge tone="success">Your fee ₹{job.captain_earning}</Badge>}
        {job.vehicle_verified && (
          <Badge tone="success">
            <BadgeCheck className="h-3 w-3" /> Vehicle verified
          </Badge>
        )}
      </div>

      {job.captain_start_stage && (job.captain_start_stage === "late" || job.captain_start_stage === "severely_late") && (
        <p className="mt-2 flex items-center gap-1.5 text-xs text-[var(--color-warning)]">
          <AlertTriangle className="h-3.5 w-3.5" />
          Started {job.captain_start_stage === "severely_late" ? "significantly late" : "late"}
          {showEarnings && job.late_penalty_pct ? ` — ${job.late_penalty_pct}% penalty on your service fee` : ""}
        </p>
      )}

      {job.before_photo && (
        <p className="mt-3 text-xs text-[var(--color-text-secondary)]">
          ✓ Before-photo captured {format(job.before_photo.captured_at)}
          {job.before_photo_flagged && <span className="ml-1 text-[var(--color-warning)]">(far from address)</span>}
        </p>
      )}
      {job.after_photo && (
        <p className="mt-1 text-xs text-[var(--color-text-secondary)]">
          ✓ After-photo captured {format(job.after_photo.captured_at)}
          {job.after_photo_flagged && <span className="ml-1 text-[var(--color-warning)]">(far from address)</span>}
          {job.status === "completed" && job.actual_duration_minutes != null && <span className="ml-1">· took {job.actual_duration_minutes} min</span>}
        </p>
      )}

      <div className="mt-4 flex gap-2">
        {action && (
          <Button className="flex-1" variant={ACTION_BUTTON_VARIANT[action.kind]} onClick={() => onAction(action.kind)}>
            <CheckCircle2 className="h-4 w-4" /> {action.label}
          </Button>
        )}
        {canReportRisk && (
          <Button variant="outline" title="Flag as running late" onClick={onReportRisk}>
            <AlertTriangle className="h-4 w-4" />
          </Button>
        )}
        {canCancel && (
          <Button variant="outline" title="Release this job" onClick={onCancel}>
            <XCircle className="h-4 w-4" />
          </Button>
        )}
      </div>
    </Card>
  );
}
