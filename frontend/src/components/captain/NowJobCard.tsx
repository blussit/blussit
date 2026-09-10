import { useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";
import { useEffect, useState } from "react";
import { AlertTriangle, BadgeCheck, CarFront, CheckCircle2, Flag, Lock, MapPin, Phone, Sparkles, XCircle, Map as MapIcon } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { MiniPinMap } from "../shared/MiniPinMap";
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

// Distinct color per stage, so the main action reads at a glance without
// reading the label — heading keeps the dark primary, then blue → amber →
// green as the job progresses toward done.
export const ACTION_BUTTON_VARIANT: Record<JobAction["kind"], "primary" | "info" | "secondary" | "success"> = {
  heading: "primary",
  verify: "info",
  before: "secondary",
  after: "success",
};

const timeOf = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }) : null;

/**
 * The "what am I doing right now" hero on the captain's jobs page — one
 * current job, its full context (customer, address + navigate, vehicle,
 * services, pay), a step timeline with real timestamps, a live service
 * timer, and ONE big next-step button. The captain seeing their own timers
 * is deliberate: measured time is the moonlighting deterrent.
 */
export function NowJobCard({
  job,
  action,
  canCancel,
  canReportRisk,
  showEarnings,
  onAction,
  onCancel,
  onReportRisk,
  onMarkUrgent,
}: {
  job: Booking;
  action: JobAction | null;
  canCancel: boolean;
  canReportRisk: boolean;
  /** Wallet system is off by default — hide per-job fee while it is. */
  showEarnings: boolean;
  onAction: (kind: JobAction["kind"]) => void;
  onCancel: () => void;
  onReportRisk: () => void;
  onMarkUrgent: () => void;
}) {
  const { t } = useCaptainTranslation();
  const [showMap, setShowMap] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const vehicleTypeName = (id: string) => vehicleTypes?.find((t) => t.id === id)?.name || id;

  // Tick the service timer every 30s while the wash is running.
  useEffect(() => {
    if (job.status !== "service_started") return;
    const t = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(t);
  }, [job.status]);

  const isFlagged = !!job.issue_flag && !job.issue_resolved;
  const minutesLeft = minutesUntilSlotStart(job.scheduled_date, job.scheduled_slot);
  const isUrgent = job.status === "assigned" && !isFlagged && minutesLeft <= URGENT_ASSIGNMENT_MINUTES;
  // The route (Navigate + pin map) exists only for the RIDE: it unlocks
  // when the captain presses t("captain.actions.headOut") (no directions before the
  // geo-stamped departure) and disappears again once he's verified the
  // vehicle — he's standing at the car, navigation is just clutter.
  const canNavigate = job.status === "captain_on_the_way" && !job.vehicle_verified;

  const steps = [
    { label: t("captain.status.assigned"), at: job.assigned_at },
    { label: "Heading", at: job.heading_at },
    { label: "Reached", at: job.vehicle_verified_at },
    { label: "Started", at: job.service_started_at },
    { label: "Done", at: job.completed_at },
  ];
  const doneCount = steps.filter((s) => !!s.at).length;

  const serviceElapsed =
    job.status === "service_started" && job.service_started_at
      ? Math.max(0, Math.round((now - new Date(job.service_started_at).getTime()) / 60000))
      : null;
  const estimate = job.duration_minutes || 60;
  const overBy = serviceElapsed != null ? serviceElapsed - estimate : null;

  return (
    <Card className={cn("overflow-hidden", isFlagged && "border-amber-400", isUrgent && "border-red-400")}>
      <div className="flex items-start justify-between gap-3 border-b border-[#F3E5B5] bg-[#FAFAFA] px-5 py-4">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-black">Now</p>
          <p className="mt-0.5 font-mono-num text-lg font-bold text-black">{job.scheduled_slot}</p>
          <p className="font-mono-num text-xs text-[var(--color-text-secondary)]">
            {format(job.scheduled_date)} · {job.booking_number}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <StatusBadge status={job.status} />
          {job.priority === "high" ? (
            <Badge tone="error">
              <Flag className="h-3 w-3" /> High priority
            </Badge>
          ) : (
            <button
              type="button"
              onClick={onMarkUrgent}
              className="flex items-center gap-1 text-[10px] font-medium text-[var(--color-text-secondary)] underline hover:text-black"
            >
              <Flag className="h-3 w-3" /> Mark urgent
            </button>
          )}
          {isFlagged && (
            <Badge tone="warning">
              <AlertTriangle className="h-3 w-3" /> {ISSUE_LABELS[job.issue_flag!] || job.issue_flag}
            </Badge>
          )}
        </div>
      </div>

      <div className="space-y-4 p-5">
        {/* Step timeline with real timestamps */}
        <div>
          <div className="flex items-center">
            {steps.map((s, i) => (
              <div key={s.label} className="flex flex-1 items-center last:flex-none">
                <div className="flex flex-col items-center">
                  <span
                    className={cn(
                      "flex h-6 w-6 items-center justify-center rounded-full border-2 text-[10px] font-bold",
                      s.at
                        ? "border-[#E8A900] bg-[#E8A900] text-white"
                        : i === doneCount
                          ? "border-[#E8A900] bg-white text-black"
                          : "border-[#F3E5B5] bg-white text-gray-300"
                    )}
                  >
                    {s.at ? <CheckCircle2 className="h-3.5 w-3.5" /> : i + 1}
                  </span>
                </div>
                {i < steps.length - 1 && <div className={cn("mx-1 h-0.5 flex-1 rounded", s.at ? "bg-[#E8A900]" : "bg-gray-100")} />}
              </div>
            ))}
          </div>
          <div className="mt-1.5 flex justify-between">
            {steps.map((s) => (
              <div key={s.label} className="flex-1 text-center first:text-left last:text-right">
                <p className="text-[10px] font-semibold text-[var(--color-text-secondary)]">{s.label}</p>
                {timeOf(s.at) && <p className="font-mono-num text-[10px] text-gray-400">{timeOf(s.at)}</p>}
              </div>
            ))}
          </div>
        </div>

        {/* Live service timer — visible measurement is the deterrent */}
        {serviceElapsed != null && (
          <div
            className={cn(
              "rounded-xl px-3.5 py-2.5 text-sm font-medium",
              overBy != null && overBy > 0 ? "bg-amber-50 text-amber-800" : "bg-[#FAFAFA] text-gray-600"
            )}
          >
            {overBy != null && overBy > 0
              ? `⏱ Running ${overBy} min over the ${estimate} min estimate — your manager can see this too.`
              : `⏱ ${serviceElapsed} min in · estimated ${estimate} min`}
          </div>
        )}

        {isUrgent && (
          <p className="flex items-center gap-1.5 text-sm font-semibold text-[var(--color-error)]">
            <AlertTriangle className="h-4 w-4" />
            {minutesLeft <= 0 ? "Starting now — head out immediately" : `Starts in ${Math.round(minutesLeft)} min — head out now`}
          </p>
        )}

        <div className="space-y-2 text-sm">
          {(job.customer_name || job.customer_phone) && (
            <div className="flex items-center gap-2 text-[var(--color-text-primary)]">
              <span className="font-semibold">{job.customer_name || t("captain.job.customer")}</span>
              {job.customer_phone && (
                <a href={`tel:${job.customer_phone}`} className="flex items-center gap-1 font-medium text-black hover:underline">
                  <Phone className="h-3.5 w-3.5" /> {job.customer_phone}
                </a>
              )}
            </div>
          )}
          <div className="flex items-start gap-2 text-[var(--color-text-secondary)]">
            <MapPin className="mt-0.5 h-4 w-4 shrink-0 text-black" />
            <span className="min-w-0 flex-1">
              {job.address_snapshot ? `${job.address_snapshot.line1}, ${job.address_snapshot.city} - ${job.address_snapshot.pincode}` : "Address unavailable"}
              {(job.travel_distance_km != null || job.travel_eta_minutes != null) && (
                <span className="mt-0.5 block text-xs">
                  🛣 {job.travel_distance_km != null ? `${job.travel_distance_km} km` : ""}
                  {job.travel_eta_minutes != null ? ` · ~${job.travel_eta_minutes} min ride` : ""}
                </span>
              )}
            </span>
            {/* Navigate only exists around the RIDE: locked chip while
                assigned (unlocks at t("captain.actions.headOut")), live while riding, and
                GONE once the vehicle is verified — he's standing at the
                car, the button is just clutter from there on. */}
            {job.address_snapshot?.latitude != null && job.address_snapshot?.longitude != null && canNavigate && (
              <a
                href={`https://www.google.com/maps/dir/?api=1&destination=${job.address_snapshot.latitude},${job.address_snapshot.longitude}`}
                target="_blank"
                rel="noreferrer"
                className="shrink-0 rounded-lg bg-black px-2.5 py-1.5 text-xs font-semibold text-white hover:opacity-90"
              >
                Navigate
              </a>
            )}
            {job.address_snapshot?.latitude != null && !canNavigate && action?.kind === "heading" && (
              <span className="flex shrink-0 cursor-not-allowed items-center gap-1 rounded-lg bg-gray-100 px-2.5 py-1.5 text-xs font-semibold text-gray-400">
                <Lock className="h-3 w-3" /> Navigate
              </span>
            )}
          </div>
          {/* Navigation (and the pin map) stay locked until the captain
              actually taps t("captain.actions.headOut") — that tap is what geo-stamps the
              start of his ride (heading_at + GPS), so letting him open
              the route beforehand would let him ride off with no record
              of when he left. */}
          {!canNavigate && action?.kind === "heading" && job.address_snapshot?.latitude != null && (
            <p className="text-xs text-gray-400">
              Tap "{t("captain.actions.headOut")}" below to unlock navigation — that's what records your departure.
            </p>
          )}
          {canNavigate && job.address_snapshot?.latitude != null && job.address_snapshot?.longitude != null && (
            <div>
              <button
                type="button"
                onClick={() => setShowMap((v) => !v)}
                className="flex items-center gap-1 text-xs font-medium text-black hover:underline"
              >
                <MapIcon className="h-3.5 w-3.5" /> {showMap ? "Hide map" : "Show on map"}
              </button>
              {showMap && <MiniPinMap latitude={job.address_snapshot.latitude} longitude={job.address_snapshot.longitude} className="mt-2" />}
            </div>
          )}
          <div className="flex items-center gap-2 text-[var(--color-text-secondary)]">
            <CarFront className="h-4 w-4 shrink-0 text-black" />
            {/* Registration number is deliberately NOT shown — the verify
                step exists so the captain types the plate they actually see. */}
            {job.vehicle_snapshot
              ? `${job.vehicle_snapshot.brand} ${job.vehicle_snapshot.model} (${vehicleTypeName(job.vehicle_snapshot.vehicle_type)})`
              : "Vehicle details unavailable"}
          </div>
          <div className="flex items-center gap-2 text-[var(--color-text-secondary)]">
            <Sparkles className="h-4 w-4 shrink-0 text-black" />
            {job.combo_name || job.service_names?.join(", ") || "Service details unavailable"}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="neutral">₹{job.total_amount}</Badge>
          {showEarnings && job.captain_earning != null && <Badge tone="success">Your fee ₹{job.captain_earning}</Badge>}
          {job.vehicle_verified && (
            <Badge tone="success">
              <BadgeCheck className="h-3 w-3" /> Vehicle verified
            </Badge>
          )}
        </div>

        {job.captain_start_stage && (job.captain_start_stage === "late" || job.captain_start_stage === "severely_late") && (
          <p className="flex items-center gap-1.5 text-xs text-[var(--color-warning)]">
            <AlertTriangle className="h-3.5 w-3.5" />
            Started {job.captain_start_stage === "severely_late" ? "significantly late" : "late"}
            {showEarnings && job.late_penalty_pct ? ` — ${job.late_penalty_pct}% penalty on your service fee` : ""}
          </p>
        )}

        {/* A missed-window booking has NO captain action — the backend
            refuses start_heading past the lockout, so instead of a button
            that errors on tap, say plainly whose move it is. */}
        {!action && job.issue_flag === "captain_missed_window" && !job.issue_resolved && (
          <p className="rounded-xl bg-[#FAFAFA] px-3.5 py-2.5 text-xs text-gray-600">
            This booking's time window has passed — your manager has to reschedule or reassign it. Nothing for you to do here right now.
          </p>
        )}
        <div className="flex gap-2">
          {action && (
            <Button className="flex-1 !py-3 text-base" variant={ACTION_BUTTON_VARIANT[action.kind]} onClick={() => onAction(action.kind)}>
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
      </div>
    </Card>
  );
}
