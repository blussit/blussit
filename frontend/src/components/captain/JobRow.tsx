import { useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";
import { AlertTriangle, CheckCircle2, Flag, XCircle } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Badge, Button, StatusBadge } from "../ui";
import { minutesUntilSlotStart, URGENT_ASSIGNMENT_MINUTES } from "../../lib/date";
import { ISSUE_LABELS } from "../../lib/constants";
import { cn } from "../../lib/cn";
import { vehicleTypeApi } from "../../api/catalog";
import type { BookingSlab } from "../../lib/bookingGroups";
import type { Booking } from "../../types";
import { ACTION_BUTTON_VARIANT, type JobAction } from "./NowJobCard";

/**
 * One row in the captain's job list — Section 8 of the BLUSSIT UX update:
 * a clean row/list instead of a large card, showing only what's needed to
 * decide what to do next. Tapping the row (or "Details") opens the full
 * BookingDetailDrawer for everything else (address, photos, timeline).
 *
 * A multi-car visit is ONE row: every car named, the visit's total, and
 * the action for the car he'd work next.
 */
export function JobRow({
  job,
  visit,
  action,
  canCancel,
  canReportRisk,
  onAction,
  onCancel,
  onReportRisk,
  onOpenDetails,
  onCollect,
}: {
  job: Booking;
  visit?: BookingSlab | null;
  action: JobAction | null;
  canCancel: boolean;
  canReportRisk: boolean;
  onAction: (kind: JobAction["kind"]) => void;
  onCancel: () => void;
  onReportRisk: () => void;
  onOpenDetails: () => void;
  /** Present only on a completed, still-unpaid job — opens the doorstep
   * collect flow (cash tap / scan-to-pay QR). */
  onCollect?: () => void;
}) {
  const { t } = useCaptainTranslation();
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const vehicleTypeName = (id: string) => vehicleTypes?.find((t) => t.id === id)?.name || id;

  const cars = visit?.isVisit ? visit.bookings : [job];
  const isVisit = cars.length > 1;
  const isFlagged = !!job.issue_flag && !job.issue_resolved;
  const minutesLeft = minutesUntilSlotStart(job.scheduled_date, job.scheduled_slot);
  const isUrgent = job.status === "assigned" && !isFlagged && minutesLeft <= URGENT_ASSIGNMENT_MINUTES;
  const status = isVisit ? visit!.status : job.status;
  const amount = isVisit ? visit!.totalAmount : job.total_amount;
  const allPaid = cars.every((c) => c.status === "cancelled" || c.payment_status === "paid");

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
          <span className="font-mono-num text-xs text-[var(--color-text-secondary)]">
            {isVisit ? cars.map((c) => c.booking_number).join(" · ") : job.booking_number}
          </span>
          <StatusBadge status={status} label={t(`captain.status.${status}` as Parameters<typeof t>[0])} />
          {isVisit && (
            <span title={t("captain.visit.tip")} className="rounded-full bg-black px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-white">
              {t("captain.visit.trip").replace("{n}", String(cars.length))}
            </span>
          )}
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
          {!isVisit && job.vehicle_snapshot && (
            <span className="text-[var(--color-text-secondary)]">
              {" "}
              · {job.vehicle_snapshot.brand} {job.vehicle_snapshot.model} ({vehicleTypeName(job.vehicle_snapshot.vehicle_type)})
            </span>
          )}
        </p>
        {isVisit ? (
          // Every car with its own service — the row IS the visit.
          <ul className="mt-0.5 space-y-0.5 text-xs text-[var(--color-text-secondary)]">
            {cars.map((c, i) => (
              <li key={c.id} className={cn(c.status === "completed" && "line-through decoration-gray-300")}>
                <span className="font-mono-num mr-1 text-gray-400">{i + 1}.</span>
                {c.vehicle_snapshot ? `${c.vehicle_snapshot.brand} ${c.vehicle_snapshot.model}` : t("captain.job.vehicleUnavailable")} —{" "}
                {c.combo_name || c.service_names?.join(", ") || "—"}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">{job.combo_name || job.service_names?.join(", ") || "Service details unavailable"}</p>
        )}
        {isUrgent && (
          <p className="mt-1 flex items-center gap-1.5 text-xs font-semibold text-[var(--color-error)]">
            <AlertTriangle className="h-3.5 w-3.5" />
            {minutesLeft <= 0 ? "Starting now — head out immediately" : `Starts in ${Math.round(minutesLeft)} min — head out now`}
          </p>
        )}
      </button>

      {/* Priority is the MANAGER's call, not the captain's — he works the
          queue he's given. The flag below is display-only. */}
      <div className="flex shrink-0 items-center gap-2">
        {job.priority === "high" && (
          <span className="hidden shrink-0 items-center gap-1 text-[10px] font-bold uppercase text-[var(--color-error)] sm:flex">
            <Flag className="h-3 w-3" /> {t("captain.job.highPriority")}
          </span>
        )}
        {action && (
          <Button size="sm" variant={ACTION_BUTTON_VARIANT[action.kind]} onClick={() => onAction(action.kind)}>
            <CheckCircle2 className="h-3.5 w-3.5" /> {action.label}
          </Button>
        )}
        {onCollect && (
          <Button size="sm" className="bg-[#E8A900] hover:bg-[#D99A00]" onClick={onCollect}>
            Collect ₹{amount}
          </Button>
        )}
        {status === "completed" && allPaid && (
          <Badge tone="success">
            <CheckCircle2 className="h-3 w-3" /> Paid
          </Badge>
        )}
        {canReportRisk && (
          <Button size="sm" variant="outline" title={t("captain.job.reportRiskTip")} onClick={onReportRisk}>
            <AlertTriangle className="h-3.5 w-3.5" />
          </Button>
        )}
        {canCancel && (
          <Button size="sm" variant="outline" title={t("captain.job.releaseTip")} onClick={onCancel}>
            <XCircle className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>
    </div>
  );
}
