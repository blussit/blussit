import { useCaptainTranslation } from "../../context/i18n/CaptainI18nContext";
import { useEffect, useState } from "react";
import { AlertTriangle, BadgeCheck, Bike, CarFront, CheckCircle2, Flag, Lock, MapPin, Phone, XCircle, Map as MapIcon } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { MiniPinMap } from "../shared/MiniPinMap";
import { Badge, Button, Card, StatusBadge } from "../ui";
import { format, minutesUntilSlotStart, URGENT_ASSIGNMENT_MINUTES } from "../../lib/date";
import { ISSUE_LABELS } from "../../lib/constants";
import { cn } from "../../lib/cn";
import { vehicleTypeApi } from "../../api/catalog";
import type { BookingSlab } from "../../lib/bookingGroups";
import type { Booking } from "../../types";

export interface JobAction {
  label: string;
  kind: "heading" | "verify" | "before" | "after";
}

// Distinct color per stage, so the main action reads at a glance without
// reading the label — heading keeps the dark primary, then gold → amber →
// green as the job progresses toward done.
export const ACTION_BUTTON_VARIANT: Record<JobAction["kind"], "primary" | "info" | "secondary" | "success"> = {
  heading: "primary",
  verify: "info",
  before: "secondary",
  after: "success",
};

const timeOf = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" }) : null;

/** "10 h 20 min", "45 min", "now" — how long until this job's window opens.
 *  Takes the dictionary-typed `t` so a typo in a key is a build error. */
type Translate = ReturnType<typeof useCaptainTranslation>["t"];

export function countdownLabel(minutes: number, t: Translate): string {
  if (minutes <= 0) return t("captain.job.startsNow");
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  const parts = [h > 0 ? `${h} ${t("captain.job.hoursShort")}` : "", m > 0 ? `${m} ${t("captain.job.minsShort")}` : ""].filter(Boolean);
  return `${parts.join(" ")} ${t("captain.job.left")}`;
}

const FINISHED = new Set(["completed", "cancelled"]);

/**
 * The "what am I doing right now" hero on the captain's jobs page: ONE job,
 * led by the SERVICE he has to perform, with the context he needs to get
 * there and ONE big next-step button carrying its own countdown.
 *
 * A multi-car visit is one job here too — `visit` carries every car, `job`
 * is the car he is working right now. He drives once (the head-out
 * button covers the visit), then works the cars one after another: each
 * has its own plate check and its own photos, so the action button names
 * the car it's for. The money shown is the visit's.
 *
 * Deliberately quiet: every explanation that used to sit on the card as a
 * paragraph is now a `title` tooltip on the thing it explains. A captain
 * standing at a gate in the sun reads a button, not a sentence.
 *
 * Missed-window jobs never reach this card — they have no captain action at
 * all, so CaptainJobsPage lists them separately instead of dressing a dead
 * job up as "NOW".
 */
export function NowJobCard({
  job,
  visit,
  action,
  canCancel,
  canReportRisk,
  showEarnings,
  onAction,
  onCancel,
  onReportRisk,
}: {
  job: Booking;
  /** Every car on this trip when there's more than one; null for a single. */
  visit?: BookingSlab | null;
  action: JobAction | null;
  canCancel: boolean;
  canReportRisk: boolean;
  /** Wallet system is off by default — hide per-job fee while it is. */
  showEarnings: boolean;
  onAction: (kind: JobAction["kind"]) => void;
  onCancel: () => void;
  onReportRisk: () => void;
}) {
  const { t } = useCaptainTranslation();
  const [showMap, setShowMap] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const { data: vehicleTypes } = useQuery({ queryKey: ["vehicle-types"], queryFn: () => vehicleTypeApi.list() });
  const typeOf = (b: Booking) => vehicleTypes?.find((v) => v.id === b.vehicle_snapshot?.vehicle_type);
  const iconFor = (b: Booking) => (/bike|scooter|two.?wheeler/i.test(typeOf(b)?.name || "") ? Bike : CarFront);

  const cars = visit?.isVisit ? visit.bookings : [job];
  const isVisit = cars.length > 1;
  const carIndex = cars.findIndex((c) => c.id === job.id);

  // One ticker drives both the service timer and the start countdown.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(id);
  }, []);

  const isFlagged = !!job.issue_flag && !job.issue_resolved;
  const minutesLeft = minutesUntilSlotStart(job.scheduled_date, job.scheduled_slot);
  const isUrgent = job.status === "assigned" && !isFlagged && minutesLeft <= URGENT_ASSIGNMENT_MINUTES;
  const waitingToStart = job.status === "assigned" && minutesLeft > 0;
  // The route exists only for the RIDE: it unlocks when the captain presses
  // "head out" (no directions before the geo-stamped departure) and
  // disappears once he's verified the vehicle — he's at the car by then.
  // On a visit "at the car" means any car: he's at the address.
  const reachedAddress = cars.some((c) => c.vehicle_verified || !!c.service_started_at || c.status === "completed");
  const canNavigate = job.status === "captain_on_the_way" && !reachedAddress;

  const steps = [
    { label: t("captain.steps.assigned"), at: job.assigned_at },
    { label: t("captain.steps.heading"), at: job.heading_at },
    { label: t("captain.steps.reached"), at: job.vehicle_verified_at },
    { label: t("captain.steps.started"), at: job.service_started_at },
    { label: t("captain.steps.done"), at: job.completed_at },
  ];
  const doneCount = steps.filter((s) => !!s.at).length;

  const serviceElapsed =
    job.status === "service_started" && job.service_started_at
      ? Math.max(0, Math.round((now - new Date(job.service_started_at).getTime()) / 60000))
      : null;
  const estimate = job.duration_minutes || 60;
  const overBy = serviceElapsed != null ? serviceElapsed - estimate : null;
  const serviceName = isVisit
    ? visit!.serviceLabel
    : job.combo_name || job.service_names?.join(", ") || t("captain.job.serviceUnavailable");
  const totalAmount = isVisit ? visit!.totalAmount : job.total_amount;
  const totalFee = cars.reduce((sum, c) => sum + (c.captain_earning ?? 0), 0);
  const hasFee = cars.some((c) => c.captain_earning != null);
  // The head-out is for the trip; every other step is for ONE car — say
  // which, so "Before photo" on car 2 can't be mistaken for car 1's.
  const actionLabel = action && isVisit && action.kind !== "heading"
    ? `${t("captain.visit.car")} ${carIndex + 1} · ${action.label}`
    : action?.label;

  return (
    <Card className={cn("overflow-hidden", isUrgent && "border-red-400")}>
      {/* THE SERVICE leads — it's what he's actually here to do. The slot,
          the date and the booking number are reference, not headline. */}
      <div className="flex items-start justify-between gap-3 border-b border-[#F3E5B5] bg-[#FAFAFA] px-5 py-4">
        <div className="min-w-0">
          <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-black">{t("captain.job.now")}</p>
          <p className="mt-0.5 font-display text-lg font-bold leading-snug text-black">{serviceName}</p>
          <p className="font-mono-num text-xs text-[var(--color-text-secondary)]">
            {job.scheduled_slot} · {format(job.scheduled_date)}
          </p>
          <p className="font-mono-num text-[11px] text-gray-400">{isVisit ? cars.map((c) => c.booking_number).join(" · ") : job.booking_number}</p>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <StatusBadge status={isVisit ? visit!.status : job.status} label={t(`captain.status.${isVisit ? visit!.status : job.status}` as Parameters<typeof t>[0])} />
          {isVisit && (
            <Badge tone="neutral" title={t("captain.visit.tip")}>
              {t("captain.visit.trip").replace("{n}", String(cars.length))}
            </Badge>
          )}
          {job.priority === "high" && (
            <Badge tone="error">
              <Flag className="h-3 w-3" /> {t("captain.job.highPriority")}
            </Badge>
          )}
          {isFlagged && (
            <Badge tone="warning" title={ISSUE_LABELS[job.issue_flag!] || job.issue_flag || undefined}>
              <AlertTriangle className="h-3 w-3" /> {ISSUE_LABELS[job.issue_flag!] || job.issue_flag}
            </Badge>
          )}
        </div>
      </div>

      <div className="space-y-4 p-5">
        {/* Step timeline with real timestamps — the current car's. */}
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

        {/* Live service timer — visible measurement is the deterrent. */}
        {serviceElapsed != null && (
          <div
            className={cn(
              "rounded-xl px-3.5 py-2.5 text-sm font-semibold",
              overBy != null && overBy > 0 ? "bg-amber-50 text-amber-800" : "bg-[#FAFAFA] text-gray-600"
            )}
            title={
              overBy != null && overBy > 0
                ? t("captain.job.overRunTip")
                : `${t("captain.job.estimated")} ${estimate} ${t("captain.job.minsShort")}`
            }
          >
            {overBy != null && overBy > 0
              ? `${serviceElapsed} ${t("captain.job.minsShort")} · +${overBy} ${t("captain.job.overBy")}`
              : `${serviceElapsed} / ${estimate} ${t("captain.job.minsShort")}`}
          </div>
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
              {job.address_snapshot
                ? `${job.address_snapshot.line1}, ${job.address_snapshot.city} - ${job.address_snapshot.pincode}`
                : t("captain.job.addressUnavailable")}
              {(job.travel_distance_km != null || job.travel_eta_minutes != null) && (
                <span className="mt-0.5 block text-xs">
                  {job.travel_distance_km != null ? `${job.travel_distance_km} km` : ""}
                  {job.travel_eta_minutes != null ? ` · ~${job.travel_eta_minutes} ${t("captain.job.minRide")}` : ""}
                </span>
              )}
            </span>
            {job.address_snapshot?.latitude != null && job.address_snapshot?.longitude != null && canNavigate && (
              <a
                href={`https://www.google.com/maps/dir/?api=1&destination=${job.address_snapshot.latitude},${job.address_snapshot.longitude}`}
                target="_blank"
                rel="noreferrer"
                className="shrink-0 rounded-lg bg-black px-2.5 py-1.5 text-xs font-semibold text-white hover:opacity-90"
              >
                {t("captain.job.navigate")}
              </a>
            )}
            {/* Locked until he taps "head out" — that tap is what records
                his departure. Explained on hover, not in a paragraph. */}
            {job.address_snapshot?.latitude != null && !canNavigate && action?.kind === "heading" && (
              <span
                title={t("captain.job.navigateLockedTip")}
                className="flex shrink-0 cursor-help items-center gap-1 rounded-lg bg-gray-100 px-2.5 py-1.5 text-xs font-semibold text-gray-400"
              >
                <Lock className="h-3 w-3" /> {t("captain.job.navigate")}
              </span>
            )}
          </div>
          {canNavigate && job.address_snapshot?.latitude != null && job.address_snapshot?.longitude != null && (
            <div>
              <button
                type="button"
                onClick={() => setShowMap((v) => !v)}
                className="flex items-center gap-1 text-xs font-medium text-black hover:underline"
              >
                <MapIcon className="h-3.5 w-3.5" /> {showMap ? t("captain.job.hideMap") : t("captain.job.showMap")}
              </button>
              {showMap && <MiniPinMap latitude={job.address_snapshot.latitude} longitude={job.address_snapshot.longitude} className="mt-2" />}
            </div>
          )}

          {/* Every car on the trip, in working order, each with where it
              stands — so "Car 2 · Before photo" on the button below has an
              obvious referent. Registration is deliberately NOT shown: the
              verify step exists so the captain types the plate he sees. */}
          {isVisit ? (
            <ul className="divide-y divide-[#F3E5B5] rounded-xl border border-[#F3E5B5]">
              {cars.map((c, i) => {
                const Icon = iconFor(c);
                const done = c.status === "completed";
                const current = c.id === job.id && !FINISHED.has(c.status);
                return (
                  <li key={c.id} className={cn("flex items-center gap-2.5 px-3 py-2", current && "bg-[#FFFCF0]")}>
                    <span className="font-mono-num w-5 shrink-0 text-xs font-bold text-gray-400">{i + 1}</span>
                    <Icon className={cn("h-4 w-4 shrink-0", current ? "text-black" : "text-gray-400")} />
                    <span className="min-w-0 flex-1">
                      <span className={cn("block truncate text-sm", current ? "font-semibold text-black" : "text-[var(--color-text-primary)]")}>
                        {c.vehicle_snapshot ? `${c.vehicle_snapshot.brand} ${c.vehicle_snapshot.model}` : t("captain.job.vehicleUnavailable")}
                        {typeOf(c) ? <span className="text-xs text-gray-400"> ({typeOf(c)!.name})</span> : null}
                      </span>
                      <span className="block truncate text-xs text-[var(--color-text-secondary)]">
                        {c.combo_name || c.service_names?.join(", ") || "—"} · <span className="font-mono-num">₹{c.total_amount}</span>
                      </span>
                    </span>
                    <span
                      className={cn(
                        "shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide",
                        done ? "bg-green-100 text-green-700" : current ? "bg-black text-white" : "bg-gray-100 text-gray-500"
                      )}
                    >
                      {done ? t("captain.visit.done") : current ? t("captain.visit.now") : t("captain.visit.next")}
                    </span>
                  </li>
                );
              })}
            </ul>
          ) : (
            <div className="flex items-center gap-2 text-[var(--color-text-secondary)]">
              {(() => {
                const Icon = iconFor(job);
                return <Icon className="h-4 w-4 shrink-0 text-black" />;
              })()}
              {job.vehicle_snapshot
                ? `${job.vehicle_snapshot.brand} ${job.vehicle_snapshot.model}${typeOf(job) ? ` (${typeOf(job)!.name})` : ""}`
                : t("captain.job.vehicleUnavailable")}
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="neutral" title={isVisit ? t("captain.visit.total") : undefined}>
            {isVisit ? `${t("captain.visit.total")} ` : ""}₹{totalAmount}
          </Badge>
          {showEarnings && hasFee && (
            <Badge tone="success">
              {t("captain.job.yourFee")} ₹{totalFee}
            </Badge>
          )}
          {job.vehicle_verified && (
            <Badge tone="success">
              <BadgeCheck className="h-3 w-3" /> {t("captain.job.vehicleVerified")}
            </Badge>
          )}
          {job.captain_start_stage === "late" || job.captain_start_stage === "severely_late" ? (
            <Badge
              tone="warning"
              title={showEarnings && job.late_penalty_pct ? `${job.late_penalty_pct}% ${t("captain.job.latePenaltyTip")}` : undefined}
            >
              <AlertTriangle className="h-3 w-3" />
              {job.captain_start_stage === "severely_late" ? t("captain.job.startedVeryLate") : t("captain.job.startedLate")}
            </Badge>
          ) : null}
        </div>

        <div className="flex gap-2">
          {action && (
            <Button
              className="flex-1 flex-col !gap-0.5 !py-3"
              variant={ACTION_BUTTON_VARIANT[action.kind]}
              onClick={() => onAction(action.kind)}
            >
              <span className="flex items-center gap-2 text-base font-semibold">
                <CheckCircle2 className="h-4 w-4" /> {actionLabel}
              </span>
              {/* The countdown rides ON the action, where he's already
                  looking — not as a separate warning line. */}
              {waitingToStart && (
                <span className={cn("text-[11px] font-medium opacity-90", isUrgent && "opacity-100")}>
                  {countdownLabel(minutesLeft, t)}
                </span>
              )}
            </Button>
          )}
          {canReportRisk && (
            <Button variant="outline" title={t("captain.job.reportRiskTip")} aria-label={t("captain.job.reportRiskTip")} onClick={onReportRisk}>
              <AlertTriangle className="h-4 w-4" />
            </Button>
          )}
          {canCancel && (
            <Button variant="outline" title={t("captain.job.releaseTip")} aria-label={t("captain.job.releaseTip")} onClick={onCancel}>
              <XCircle className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>
    </Card>
  );
}
