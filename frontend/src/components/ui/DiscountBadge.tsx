import { Sparkles } from "lucide-react";
import { cn } from "../../lib/cn";

/** Whole-percent saving of `price` against the struck-through MRP, or null
 *  when there's no real saving worth advertising (< 5%). */
export function discountPercent(price: number, original: number | null | undefined): number | null {
  if (original == null || !(original > price) || original <= 0) return null;
  const pct = Math.round(((original - price) / original) * 100);
  return pct >= 5 ? pct : null;
}

/** The one "X% OFF" mark used on every service card (landing, /services,
 *  booking flow) — red with a sparkle, nothing else. */
export function DiscountBadge({ percent, className }: { percent: number | null; className?: string }) {
  if (percent == null) return null;
  return (
    <span
      className={cn(
        "relative inline-flex shrink-0 items-center gap-1 overflow-hidden rounded-full bg-[#E11D48] px-2 py-0.5 text-[11px] font-bold leading-4 text-white",
        className,
      )}
    >
      <span className="pointer-events-none absolute inset-0 bg-[linear-gradient(110deg,transparent_0%,rgba(255,255,255,0.28)_30%,transparent_55%)] animate-sheen-drift" />
      <Sparkles className="relative h-3 w-3" />
      <span className="relative">{percent}% OFF</span>
    </span>
  );
}

/** Admin-set offer label (Service.offer_tag), e.g. "Special offer". */
export function OfferTag({ label, className }: { label?: string | null; className?: string }) {
  if (!label?.trim()) return null;
  return (
    <span className={cn("inline-flex shrink-0 items-center rounded-full bg-[#0E1A33] px-2 py-0.5 text-[11px] font-bold leading-4 text-white", className)}>
      {label.trim()}
    </span>
  );
}
