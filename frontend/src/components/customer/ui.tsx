/**
 * Customer app building blocks (theme v2): white ground, navy type
 * (#0E1A33), one blue (#0A66F0) for actions and "active", muted #5F6878,
 * light tints #E8F0FE / #EEF3FA, hairline borders #E4E9F1, 14–18px radii,
 * soft shadows. Yellow (#FFD21F, navy text) only for the rare highlight CTA.
 * Shared by every /app page so the screens read as one app.
 */
import type { ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Ban, CalendarCheck, CheckCircle2, ChevronLeft, ChevronRight, Clock3, CreditCard, Navigation2, Sparkles, UserCheck, type LucideIcon } from "lucide-react";
import { cn } from "../../lib/cn";
import { customerStatusLabel } from "../../lib/customerStatus";
import { titleCase } from "../public/landing/shared";

export const NAVY = "#0E1A33";
export const BLUE = "#0A66F0";

/** The one card surface every customer screen uses. */
export const card = "rounded-2xl border border-[#E4E9F1] bg-white shadow-[0_1px_2px_rgba(14,26,51,0.04),0_10px_28px_-18px_rgba(14,26,51,0.18)]";

const btnBase =
  "shrink-0 items-center justify-center gap-1.5 whitespace-nowrap font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50";
const sizes = { sm: "h-9 rounded-xl px-3.5 text-[13px]", md: "h-11 rounded-xl px-4 text-sm", lg: "h-[52px] rounded-2xl px-5 text-[15px]" } as const;
const tones = {
  primary: "bg-[#0A66F0] text-white shadow-[0_8px_18px_-8px_rgba(10,102,240,0.55)] hover:bg-[#0857D0]",
  outline: "border border-[#CFDCF0] bg-white text-[#0A66F0] hover:border-[#0A66F0] hover:bg-[#F5F9FF]",
  soft: "bg-[#E8F0FE] text-[#0A66F0] hover:bg-[#DCE8FD]",
  ghost: "text-[#5F6878] hover:bg-[#EEF3FA] hover:text-[#0E1A33]",
  yellow: "bg-[#FFD21F] text-[#0E1A33] hover:bg-[#F5C400]",
  danger: "border border-[#F6D3D3] bg-white text-[#C62828] hover:bg-[#FFF5F5]",
} as const;

/** Class string for a customer button — use on <button>, <Link> or <a>. */
export function btn(tone: keyof typeof tones = "primary", size: keyof typeof sizes = "md", extra?: string) {
  // A responsive "hidden sm:inline-flex" in `extra` owns the display itself.
  return cn(/(^|\s)hidden(\s|$)/.test(extra || "") ? "" : "inline-flex", btnBase, sizes[size], tones[tone], extra);
}

/** Screen title row: optional back arrow, title, optional right-side action. */
export function PageHeader({ title, subtitle, back, right }: { title: ReactNode; subtitle?: ReactNode; back?: boolean | string; right?: ReactNode }) {
  const navigate = useNavigate();
  return (
    <div className="flex items-center gap-2">
      {back && (
        <button
          type="button"
          aria-label="Back"
          onClick={() => (typeof back === "string" ? navigate(back) : window.history.length > 1 ? navigate(-1) : navigate("/app"))}
          className="-ml-2 flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-[#0E1A33] transition-colors hover:bg-[#EEF3FA]"
        >
          <ChevronLeft className="h-5 w-5" />
        </button>
      )}
      <div className="min-w-0 flex-1">
        <h1 className="truncate font-display text-[22px] font-bold leading-tight text-[#0E1A33] lg:text-[26px]">{title}</h1>
        {subtitle && <p className="mt-0.5 truncate text-sm text-[#5F6878]">{subtitle}</p>}
      </div>
      {right}
    </div>
  );
}

export function SectionTitle({ title, to, linkLabel, right }: { title: ReactNode; to?: string; linkLabel?: string; right?: ReactNode }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-3">
      <h2 className="font-display text-[16px] font-bold text-[#0E1A33]">{title}</h2>
      {to && linkLabel ? (
        <Link to={to} className="inline-flex items-center gap-0.5 text-[13px] font-semibold text-[#0A66F0] hover:underline">
          {linkLabel} <ChevronRight className="h-4 w-4" />
        </Link>
      ) : (
        right
      )}
    </div>
  );
}

/** Rounded icon square in the blue tint. */
export function IconTile({ icon: Icon, className, iconClassName }: { icon: LucideIcon; className?: string; iconClassName?: string }) {
  return (
    <span className={cn("flex h-11 w-11 shrink-0 items-center justify-center rounded-[14px] bg-[#E8F0FE] text-[#0A66F0]", className)}>
      <Icon className={cn("h-5 w-5", iconClassName)} />
    </span>
  );
}

export function Skeleton({ className }: { className: string }) {
  return <div aria-hidden="true" className={cn("animate-pulse rounded-2xl bg-[#EEF3FA]", className)} />;
}

/** A booking status as the customer reads it: tone + icon + words. */
const STATUS_STYLE: Record<string, { cls: string; icon: LucideIcon }> = {
  awaiting_payment: { cls: "bg-[#FFF4E5] text-[#B25E00]", icon: CreditCard },
  pending: { cls: "bg-[#FFF4E5] text-[#B25E00]", icon: Clock3 },
  rescheduled: { cls: "bg-[#FFF4E5] text-[#B25E00]", icon: CalendarCheck },
  assigned: { cls: "bg-[#E7F6EC] text-[#1E7B3C]", icon: UserCheck },
  captain_on_the_way: { cls: "bg-[#E8F0FE] text-[#0A66F0]", icon: Navigation2 },
  service_started: { cls: "bg-[#E8F0FE] text-[#0A66F0]", icon: Sparkles },
  completed: { cls: "bg-[#E7F6EC] text-[#1E7B3C]", icon: CheckCircle2 },
  cancelled: { cls: "bg-[#FDECEC] text-[#C62828]", icon: Ban },
};

export function StatusChip({ status, className }: { status: string; className?: string }) {
  const style = STATUS_STYLE[status] || { cls: "bg-[#EEF3FA] text-[#5F6878]", icon: Clock3 };
  const Icon = style.icon;
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-semibold", style.cls, className)}>
      <Icon className="h-3.5 w-3.5" />
      {titleCase(customerStatusLabel(status) || status.replace(/_/g, " "))}
    </span>
  );
}

/** Segmented control (Upcoming | Past). */
export function Segmented<T extends string>({ value, options, onChange }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div role="tablist" className="grid rounded-2xl bg-[#EEF3FA] p-1" style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))` }}>
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(o.value)}
            className={cn(
              "h-10 rounded-xl text-sm font-semibold transition-all",
              active ? "bg-white text-[#0E1A33] shadow-[0_1px_3px_rgba(14,26,51,0.12)]" : "text-[#5F6878] hover:text-[#0E1A33]"
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/** Profile / settings list row. */
export function MenuRow({
  icon: Icon,
  label,
  meta,
  to,
  href,
  onClick,
  danger,
  badge,
}: {
  icon: LucideIcon;
  label: string;
  meta?: ReactNode;
  to?: string;
  href?: string;
  onClick?: () => void;
  danger?: boolean;
  badge?: number;
}) {
  const inner = (
    <>
      <span className={cn("flex h-10 w-10 shrink-0 items-center justify-center rounded-xl", danger ? "bg-[#FDECEC] text-[#C62828]" : "bg-[#EEF3FA] text-[#0E1A33]")}>
        <Icon className="h-[18px] w-[18px]" />
      </span>
      <span className={cn("min-w-0 flex-1 truncate text-left text-[15px] font-medium", danger ? "text-[#C62828]" : "text-[#0E1A33]")}>{label}</span>
      {!!badge && (
        <span className="inline-flex min-w-[1.25rem] items-center justify-center rounded-full bg-[#0A66F0] px-1.5 py-0.5 text-[11px] font-bold text-white">
          {badge > 99 ? "99+" : badge}
        </span>
      )}
      {meta && <span className="shrink-0 text-xs text-[#5F6878]">{meta}</span>}
      {!danger && <ChevronRight className="h-4 w-4 shrink-0 text-[#A3ADBD]" />}
    </>
  );
  const cls = "flex w-full items-center gap-3.5 px-4 py-3 transition-colors hover:bg-[#F7F9FC]";
  if (to) {
    return (
      <Link to={to} className={cls}>
        {inner}
      </Link>
    );
  }
  if (href) {
    return (
      <a href={href} className={cls}>
        {inner}
      </a>
    );
  }
  return (
    <button type="button" onClick={onClick} className={cls}>
      {inner}
    </button>
  );
}

/** "Good Morning" / "Good Afternoon" / "Good Evening" by the IST clock. */
export function greetingIST(now = new Date()): string {
  const hour = Number(new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", hour: "numeric", hourCycle: "h23" }).format(now));
  if (hour >= 5 && hour < 12) return "Good Morning";
  if (hour >= 12 && hour < 17) return "Good Afternoon";
  return "Good Evening";
}

/** wa.me link for an Indian mobile number (10 digits → 91 prefix). */
export function whatsAppLink(phone?: string | null): string | null {
  const digits = String(phone || "").replace(/\D/g, "");
  if (digits.length < 10) return null;
  return `https://wa.me/${digits.length === 10 ? `91${digits}` : digits}`;
}

/** WhatsApp glyph (lucide ships no brand icons). */
export function WhatsAppGlyph({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" className={className}>
      <path d="M12.04 2C6.58 2 2.13 6.45 2.13 11.91c0 1.75.46 3.45 1.32 4.95L2.05 22l5.25-1.38a9.9 9.9 0 0 0 4.74 1.21h.01c5.46 0 9.9-4.45 9.9-9.91 0-2.65-1.03-5.14-2.9-7.01A9.82 9.82 0 0 0 12.04 2Zm0 18.15h-.01a8.23 8.23 0 0 1-4.19-1.15l-.3-.18-3.12.82.83-3.04-.2-.31a8.2 8.2 0 0 1-1.26-4.38c0-4.54 3.7-8.24 8.25-8.24 2.2 0 4.27.86 5.83 2.42a8.18 8.18 0 0 1 2.41 5.83c0 4.54-3.7 8.23-8.24 8.23Zm4.52-6.16c-.25-.12-1.47-.72-1.69-.81-.23-.08-.39-.12-.56.13-.17.25-.64.81-.78.97-.14.17-.29.19-.54.06-.25-.12-1.05-.39-1.99-1.23-.74-.66-1.23-1.47-1.38-1.72-.14-.25-.02-.38.11-.51.11-.11.25-.29.37-.43.13-.15.17-.25.25-.42.08-.17.04-.31-.02-.43-.06-.12-.56-1.34-.76-1.84-.2-.48-.41-.42-.56-.43h-.48c-.17 0-.43.06-.66.31-.23.25-.86.85-.86 2.07 0 1.22.89 2.4 1.01 2.56.12.17 1.75 2.67 4.23 3.74.59.26 1.05.41 1.41.52.59.19 1.13.16 1.56.1.48-.07 1.47-.6 1.67-1.18.21-.58.21-1.07.14-1.18-.06-.1-.22-.16-.47-.29Z" />
    </svg>
  );
}
