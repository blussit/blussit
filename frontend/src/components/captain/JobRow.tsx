import { useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";
import { AlertTriangle, CheckCircle2, Flag, XCircle } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Badge, Button, StatusBadge } from "../ui";
import { minutesUntilSlotStart, URGENT_ASSIGNMENT_MINUTES } from "../../lib/date";
import { ISSUE_LABELS } from "../../lib/constants";
import { cn } from "../../lib/cn";
import { vehicleTypeApi } from "../../api/catalog";
import type { Booking } from "../../types";
import { ACTION_BUTTON_VARIANT, type JobAction } from "./NowJobCard";

/**
 * One row in the captain's job list — Section 8 of the BLUSSIT UX update:
 * a clean row/list instead of a large card, showing only what's needed to
 * decide what to do next. Tapping the row (or "Details") opens the full
 * BookingDetailDrawer for everything else (address, photos, timeline).
 */
export function JobRow({
  job,
  action,
  canCancel,
  canReportRisk,
  onAction,
  onCancel,
  onReportRisk,
  onMarkUrgent,
  onOpenDetails,
  onCollect,
}: {
  job: Booking;
  action: JobAction | null;
  canCancel: boolean;
  canReportRisk: boolean;
  onAction: (kind: JobAction["kind"]) => void;
  onCancel: () => void;
  onReportRisk: () => void;
  onMarkUrgent: () => void;
  onOpenDetails: () => void;
  /** Present only on a completed, still-unpaid job — opens the doorstep
   * collect flow (cash tap / scan-to-pay QR). */
  onCollect?: () => void;
}) {
  const { t } = useCaptainTranslation();
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const vehicleTypeName = (id: string) => vehicleTypes?.find((t) => t.id === id)?.name || id;

  const isFlagged = !!job.issue_flag && !job.issue_resolved;
  const minutesLeft = minutesUntilSlotStart(job.scheduled_date, job.scheduled_slot);
  const isUrgent = job.status === "assigned" && !isFlagged && minutesLeft <= URGENT_ASSIGNMENT_MINUTES;

  return (
    <div
      className={cn(
        "flex flex-col gap-3 rounded-[var(--radius-card)] border border-[#E5E7EB] bg-white p-4 transition-colors hover:border-[#E8A900] sm:flex-row sm:items-center sm:justify-between cursor-pointer",
        isFlagged && "bg-amber-50/40",
        isUrgent && "bg-red-50/40"
      )}
      onClick={(e) => {
        // Prevent opening details if clicking a specific action button
        if (!(e.target as HTMLElement).closest("button:not(.job-row-main-btn)")) {
          onOpenDetails();
        }
      }}
    >
      <button type="button" className="job-row-main-btn flex-1 text-left">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono-num text-sm font-semibold text-[var(--color-text-primary)]">{job.scheduled_slot}</span>
          <span className="font-mono-num text-xs text-[var(--color-text-secondary)]">{job.booking_number}</span>
          <StatusBadge status={job.status} />
          {job.priority === "high" && (
            <Badge tone="error">
              <Flag className="h-3 w-3" /> High
            </Badge>
          )}
          {isFlagged && (
            <Badge tone="warning">
              <AlertTriangle className="h-3 w-3" /> {ISSUE_LABELS[job.issue_flag!] || job.issue_flag}
            </Badge>
          )}
        </div>
        <p className="mt-1 text-sm text-[var(--color-text-primary)]">
          {job.customer_name || t("captain.job.customer")}
          {job.vehicle_snapshot && (
            <span className="text-[var(--color-text-secondary)]">
              {" "}
              · {job.vehicle_snapshot.brand} {job.vehicle_snapshot.model} ({vehicleTypeName(job.vehicle_snapshot.vehicle_type)})
            </span>
          )}
        </p>
        <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">{job.combo_name || job.service_names?.join(", ") || "Service details unavailable"}</p>
        {isUrgent && (
          <p className="mt-1 flex items-center gap-1.5 text-xs font-semibold text-[var(--color-error)]">
            <AlertTriangle className="h-3.5 w-3.5" />
            {minutesLeft <= 0 ? "Starting now — head out immediately" : `Starts in ${Math.round(minutesLeft)} min — head out now`}
          </p>
        )}
      </button>

      <div className="flex shrink-0 items-center gap-2">
        {!job.priority || job.priority !== "high" ? (
          job.status !== "completed" &&
          job.status !== "cancelled" && (
            <button
              type="button"
              onClick={onMarkUrgent}
              className="hidden shrink-0 items-center gap-1 text-[10px] font-medium text-[var(--color-text-secondary)] underline hover:text-[var(--color-text-primary)] sm:flex"
            >
              <Flag className="h-3 w-3" /> Mark urgent
            </button>
          )
        ) : null}
        {action && (
          <Button size="sm" variant={ACTION_BUTTON_VARIANT[action.kind]} onClick={() => onAction(action.kind)}>
            <CheckCircle2 className="h-3.5 w-3.5" /> {action.label}
          </Button>
        )}
        {onCollect && (
          <Button size="sm" className="bg-[#E8A900] hover:bg-[#D99A00]" onClick={onCollect}>
            Collect ₹{job.total_amount}
          </Button>
        )}
        {job.status === "completed" && job.payment_status === "paid" && (
          <Badge tone="success">
            <CheckCircle2 className="h-3 w-3" /> Paid
          </Badge>
        )}
        {canReportRisk && (
          <Button size="sm" variant="outline" title="Flag as running late" onClick={onReportRisk}>
            <AlertTriangle className="h-3.5 w-3.5" />
          </Button>
        )}
        {canCancel && (
          <Button size="sm" variant="outline" title="Release this job" onClick={onCancel}>
            <XCircle className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>
    </div>
  );
}

