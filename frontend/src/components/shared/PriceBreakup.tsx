import { cn } from "../../lib/cn";
import { toTitle } from "../../lib/titleCase";
import type { Bill } from "../../lib/bill";

const money = (n: number) => `₹${+Number(n || 0).toFixed(2)}`;

export interface BillCar {
  id: string;
  /** "Sedan · Star Wash" — shown above that car's lines on a multi-car visit. */
  label?: string;
  bill?: Bill | null;
}

type RowProps = { label: React.ReactNode; value: React.ReactNode; tone?: "green"; sub?: React.ReactNode; testId?: string };

function BillRow({ label, value, tone, sub, testId }: RowProps) {
  return (
    <div className={cn("flex items-start justify-between gap-4 text-sm", tone === "green" && "text-[#1E7B3C]")} data-testid={testId}>
      <span className={cn("min-w-0", tone !== "green" && "text-[var(--color-text-secondary)]")}>
        {label}
        {sub && <span className="block text-xs">{sub}</span>}
      </span>
      <span className={cn("shrink-0 text-right tabular-nums", tone !== "green" && "font-medium text-[var(--color-text-primary)]")}>{value}</span>
    </div>
  );
}

const TIP_LABEL: Record<string, string> = { cash: "Cash", online: "Online" };

/**
 * The price rows of a booking or a whole visit, from the server's `bill`:
 * each service (add-ons marked) at the price charged, the coupon / plan /
 * manager discount, distance charge, previous balance, extras added on site
 * and the tip. The caller adds the Total (and wallet / paid / due) below.
 * `staff` only changes the wording of the manager's own discount.
 */
export function PriceBreakup({ cars, staff = false }: { cars: BillCar[]; staff?: boolean }) {
  const billed = cars.filter((c): c is BillCar & { bill: Bill } => !!c.bill);
  if (!billed.length) return null;
  const multi = billed.length > 1;
  const sum = (pick: (b: Bill) => number) => billed.reduce((n, c) => n + pick(c.bill), 0);
  const onSite = billed.flatMap((c) => c.bill.on_site);
  const tip = billed.find((c) => c.bill.tip)?.bill.tip ?? null;
  const travel = billed.find((c) => c.bill.travel)?.bill.travel ?? null;
  const travelAmount = sum((b) => b.travel?.amount || 0);
  const managerDiscount = sum((b) => b.manager_discount);
  const carried = sum((b) => b.carried);
  const cancellation = sum((b) => b.cancellation);
  const other = sum((b) => b.other);
  const tipAmount = sum((b) => b.tip?.amount || 0);

  return (
    <div className="space-y-2" data-testid="price-breakup">
      {billed.map((c) => {
        const b = c.bill;
        return (
          <div key={c.id} className="space-y-2">
            {multi && c.label && <p className="pt-1 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-primary)]">{c.label}</p>}
            {b.lines.map((l, i) => (
              <BillRow
                key={`${l.service_id || l.name}-${i}`}
                label={
                  <>
                    {l.is_addon ? "Add-On · " : ""}
                    {toTitle(l.name) || "Service"}
                    {l.qty > 1 ? ` ×${l.qty}` : ""}
                  </>
                }
                value={
                  <>
                    {l.regular_amount ? <span className="mr-1.5 text-xs font-normal text-[var(--color-text-secondary)] line-through">{money(l.regular_amount)}</span> : null}
                    {money(l.amount)}
                  </>
                }
                sub={l.regular_amount ? "First-wash price" : undefined}
              />
            ))}
            {b.adjustment !== 0 && <BillRow label="Price Adjustment" value={`${b.adjustment < 0 ? "−" : ""}${money(Math.abs(b.adjustment))}`} />}
            {b.discount && (
              <BillRow
                tone="green"
                testId={b.discount.kind === "plan" ? "plan-covered" : "coupon-discount"}
                label={b.discount.kind === "coupon" ? `Coupon ${b.discount.code || ""}`.trim() : b.discount.kind === "plan" ? "Covered By Plan" : "Discount"}
                value={`−${money(b.discount.amount)}`}
              />
            )}
          </div>
        );
      })}
      {managerDiscount > 0 && <BillRow tone="green" label={staff ? "Manager Discount" : "Discount"} value={`−${money(managerDiscount)}`} />}
      {onSite.map((e, i) => (
        <BillRow key={`site-${i}`} label={`Added On Site · ${toTitle(e.name) || "Extra Service"}${e.qty > 1 ? ` ×${e.qty}` : ""}`} value={money(e.amount)} />
      ))}
      {travelAmount > 0 && (
        <BillRow label={`Distance Charge${travel?.km ? ` · ${Math.round(travel.km * 10) / 10} km` : ""}`} value={money(travelAmount)} />
      )}
      {carried > 0 && <BillRow testId="booking-previous-balance" label="Previous Balance Due" value={money(carried)} />}
      {cancellation > 0 && <BillRow testId="booking-carried-charge" label="Previous Cancellation Charge" value={money(cancellation)} />}
      {tipAmount > 0 && <BillRow label={`Tip${tip ? ` · ${TIP_LABEL[tip.method] || toTitle(tip.method)}` : ""}`} value={money(tipAmount)} />}
      {other !== 0 && <BillRow label="Other Adjustment" value={`${other < 0 ? "−" : ""}${money(Math.abs(other))}`} />}
    </div>
  );
}
