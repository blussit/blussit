/**
 * A plan booking's bill, split the way every screen shows it: what the plan
 * pays for ("Covered By Plan") and what the customer pays on top ("Add-Ons
 * (Paid)" — booked with the wash or added on site). The server builds it
 * (`plan_split`, BookingService._plan_split_view); nothing here prices anything.
 */
import { toTitle } from "./titleCase";

export interface PlanSplitLine {
  service_id?: string | null;
  name?: string | null;
  qty?: number | null;
  amount?: number | null;
  by_name?: string | null;
  role?: string | null;
}

export interface PlanSplit {
  covered_amount: number;
  covers: PlanSplitLine[];
  extras_booked: PlanSplitLine[];
  extras_booked_total: number;
  extras_on_site: PlanSplitLine[];
  extras_on_site_total: number;
  extras_total: number;
}

export interface PlanSplitTotals {
  /** The plan's value for the wash(es) — what the customer is not charged. */
  covered: number;
  coveredNames: string[];
  /** Paid add-ons (booked + on site) and what they add up to. */
  extras: number;
  extraLines: { name: string; amount: number; onSite: boolean }[];
}

const label = (l: PlanSplitLine) => `${toTitle(l.name) || "Service"}${(l.qty ?? 1) > 1 ? ` ×${l.qty}` : ""}`;

/** Totals over a visit's cars; null when no car was paid for by a plan. */
export function planSplitTotals(cars: { plan_split?: PlanSplit | null }[]): PlanSplitTotals | null {
  const splits = cars.map((c) => c.plan_split).filter((s): s is PlanSplit => !!s);
  if (!splits.length) return null;
  return {
    covered: splits.reduce((n, s) => n + (s.covered_amount || 0), 0),
    coveredNames: splits.flatMap((s) => s.covers.map(label)),
    extras: splits.reduce((n, s) => n + (s.extras_total || 0), 0),
    extraLines: splits.flatMap((s) => [
      ...s.extras_booked.map((l) => ({ name: label(l), amount: l.amount || 0, onSite: false })),
      ...s.extras_on_site.map((l) => ({ name: label(l), amount: l.amount || 0, onSite: true })),
    ]),
  };
}
