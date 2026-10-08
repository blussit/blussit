import { type HTMLAttributes } from "react";
import { cn } from "../../lib/cn";
import { toTitle } from "../../lib/titleCase";

type Tone = "neutral" | "primary" | "success" | "warning" | "error" | "info";

const toneClasses: Record<Tone, string> = {
  neutral: "bg-gray-100 text-gray-700",
  primary: "bg-[var(--color-primary-light)] text-[var(--color-primary)]",
  success: "bg-[var(--ui-success-bg,var(--color-accent-light))] text-[var(--color-success)]",
  warning: "bg-amber-50 text-amber-700",
  error: "bg-red-50 text-[var(--color-error)]",
  // Light blue tint + blue text — never a solid fill.
  info: "bg-[var(--ui-tint,#E8F0FE)] text-[var(--ui-tint-ink,#0A66F0)]",
};

export function Badge({ tone = "neutral", className, ...props }: HTMLAttributes<HTMLSpanElement> & { tone?: Tone }) {
  return (
    <span
      className={cn("inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium", toneClasses[tone], className)}
      {...props}
    />
  );
}

const statusToneMap: Record<string, Tone> = {
  awaiting_payment: "error",
  pending: "warning",
  assigned: "info",
  captain_on_the_way: "info",
  service_started: "primary",
  completed: "success",
  cancelled: "error",
  rescheduled: "neutral",
  active: "success",
  expired: "neutral",
  suspended: "error",
  open: "warning",
  in_progress: "info",
  resolved: "success",
  closed: "neutral",
};

/** Statuses whose raw value doesn't read well to a customer. */
const statusLabelMap: Record<string, string> = {
  awaiting_payment: "Payment Pending",
};

export function StatusBadge({ status, label }: { status: string | null | undefined; label?: string }) {
  // Defensive: a null/undefined/empty status (a legacy record from before a
  // status field existed, a partially-loaded row, bad data) must render as
  // "unknown" rather than throw — this component has no error boundary
  // above it anywhere it's used, so an uncaught crash here white-screens
  // the entire page, not just this one badge.
  if (!status) return <Badge tone="neutral">Unknown</Badge>;
  const tone = statusToneMap[status] || "neutral";
  // `label` lets a localized surface (the captain panel) pass its own
  // wording while every other caller keeps the shared English one.
  return <Badge tone={tone}>{label || statusLabelMap[status] || toTitle(status)}</Badge>;
}
