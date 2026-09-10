import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, type LucideIcon } from "lucide-react";
import { cn } from "../../lib/cn";

/**
 * The dashboard stat tile, modeled on the Razorpay console (founder's
 * reference): a flat white card with a thin hairline, ONE small label,
 * ONE big number, and at most one line of context. Anything else lives
 * behind the drill-down — the "View all →" link only appears on hover
 * (always visible on touch, where there is no hover), so the resting
 * state stays quiet.
 *
 * Deliberately NOT a generic card: if a number needs three sub-figures,
 * that's a table on the drill-down page, not more text in this tile.
 */
export function StatCard({
  label,
  labelAfter,
  value,
  hint,
  icon: Icon,
  to,
  linkLabel = "View all",
  tone = "default",
  className,
}: {
  label: string;
  /** Tiny adornment after the label (e.g. an info tip) — nothing bigger. */
  labelAfter?: ReactNode;
  value: ReactNode;
  /** One short line of context under the number (e.g. "from 6 washes"). */
  hint?: ReactNode;
  icon?: LucideIcon;
  /** Drill-down target — makes the whole card clickable. */
  to?: string;
  linkLabel?: string;
  tone?: "default" | "success" | "warning" | "error" | "muted";
  className?: string;
}) {
  const valueTone = {
    default: "text-black",
    success: "text-[var(--color-success)]",
    warning: "text-amber-600",
    error: "text-[var(--color-error)]",
    muted: "text-gray-300",
  }[tone];

  const body = (
    <div
      className={cn(
        "group relative flex h-full flex-col rounded-2xl border border-[#F3E5B5] bg-white p-5 transition-colors",
        to && "hover:border-black",
        className
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="flex items-center gap-1.5 text-[13px] font-medium text-gray-500">
          {Icon && <Icon className="h-3.5 w-3.5 shrink-0" />}
          {label}
          {labelAfter}
        </span>
        {to && (
          <span className="flex shrink-0 items-center gap-1 text-xs font-semibold text-black opacity-0 transition-opacity group-hover:opacity-100 max-sm:opacity-100">
            {linkLabel} <ArrowRight className="h-3 w-3" />
          </span>
        )}
      </div>
      <p className={cn("font-mono-num mt-3 text-[28px] font-bold leading-none", valueTone)}>{value}</p>
      {hint && <p className="mt-1.5 text-xs text-gray-400">{hint}</p>}
    </div>
  );

  return to ? (
    <Link to={to} className="block h-full">
      {body}
    </Link>
  ) : (
    body
  );
}

/**
 * The section shell that goes with it: a plain white panel with a quiet
 * header row (title left, actions right) and no inner chrome — same
 * restraint as the tiles above.
 */
export function Panel({
  title,
  description,
  actions,
  children,
  className,
}: {
  title?: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("rounded-2xl border border-[#F3E5B5] bg-white", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[#F3E5B5] px-5 py-4">
          <div className="min-w-0">
            {title && <h2 className="font-semibold text-black">{title}</h2>}
            {description && <p className="mt-0.5 text-xs text-gray-400">{description}</p>}
          </div>
          {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}
