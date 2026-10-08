/**
 * A manager-built custom plan (spec 2026-10-07 §1.6), as the customer sees
 * it: "Custom Plan — 2 Cars", then each car (plate + type) with its own
 * per-service washes ("Deep Cleaning · 1 Of 2 Left") and its
 * "Last Booking Day: 18 Oct 2026". An unpaid cart shows its pay_label
 * ("Pay ₹X To Activate", its Razorpay link). Customers can't extend or
 * renew — a manager does; near the end the card offers "Ask Us To Renew"
 * (WhatsApp).
 *
 * Renewal (PLANS-2): a renewal is its own cart (`renewal_of`). While it is
 * upcoming it sits inside the plan it renews — unpaid: a highlighted
 * "Renew Your Plan — New Period Starts …" with "Pay ₹X To Renew"; paid: "Next
 * Period Starts …" with its per-service counts. Its passes are `scheduled`
 * until then (never bookable). A refunded car reads "Refunded ₹X To Your
 * Wallet"; a fully refunded cart is Past.
 *
 * "Book A Plan Wash" opens PlanWashSheet: one exact car, one of its
 * services with washes left (vehicle_id + subscription_id on the line).
 */
import { useState } from "react";
import { CalendarClock, Layers, RefreshCw } from "lucide-react";
import { bookableCar, carsFromCart, PlanWashSheet } from "./PlanWashSheet";
import type { CustomPlanCar, CustomPlanCart } from "../../api/customPlansMe";
import { Badge } from "../ui";
import { VehicleIcon } from "../shared/VehicleIcon";
import { openBlussitWhatsApp } from "../public/WhatsAppFloatingButton";
import { toTitle } from "../../lib/titleCase";
import { cn } from "../../lib/cn";
import { btn, card, WhatsAppGlyph } from "./ui";
import { dayLabel, daysUntil, lastBookingDay } from "./passDates";
import { customPlanTotals, paidRefundedText } from "../../lib/customPlanTotals";

/** "Ask Us To Renew" shows this many days before the plan's last booking day. */
const RENEW_PROMPT_DAYS = 3;
const UNPAID = ["awaiting_payment", "activating", "needs_review"];

const passStatus = (car: CustomPlanCar) => String(car.subscription?.status || "");
const isRefunded = (car: CustomPlanCar) => car.status === "refunded";
/** A paid renewal's pass waiting for its period ("Starts 18 Oct 2026"). */
const carScheduled = (car: CustomPlanCar) => !isRefunded(car) && passStatus(car) === "scheduled";
/** A car's pass is usable now (the server's effective status). */
const carActive = (car: CustomPlanCar) => !isRefunded(car) && passStatus(car) === "active";
/** Still running: usable now, or lined up to start. */
const carLive = (car: CustomPlanCar) => carActive(car) || carScheduled(car);

export const customPlanIsLive = (cart: CustomPlanCart) =>
  UNPAID.includes(cart.status) || (cart.status === "active" && cart.cars.some(carLive));

/** A paid cart whose every live car is still waiting for its start. */
const allScheduled = (cart: CustomPlanCart) => cart.status === "active" && cart.cars.some(carScheduled) && !cart.cars.some(carActive);

/** Only a real https link is ever opened. */
export const customPlanPayUrl = (cart: CustomPlanCart) => {
  const url = cart.payment_link?.short_url || "";
  return cart.status === "awaiting_payment" && /^https:\/\//.test(url) ? url : null;
};
export const customPlanPayAmount = (cart: CustomPlanCart) => Math.round(cart.payment_link?.amount || cart.total_amount || 0);
/** "Pay ₹1499 To Renew" — the server's words, else built the same way. */
const payLabel = (cart: CustomPlanCart) => cart.pay_label || `Pay ₹${customPlanPayAmount(cart)} To ${cart.renewal_of ? "Renew" : "Activate"}`;

/** When a renewal's new period starts: its own label, else its first car's start. */
const renewalStart = (r: CustomPlanCart) => {
  const firstCar = [...r.cars].filter((c) => c.starts_on).sort((a, b) => String(a.starts_on).localeCompare(String(b.starts_on)))[0];
  return dayLabel(r.renewal_starts_on_label, r.renewal_starts_on) || dayLabel(firstCar?.starts_on_label, firstCar?.starts_on);
};

/** A renewal that is still ahead (unpaid, or paid with every pass scheduled). */
const upcomingRenewal = (r: CustomPlanCart) => !!r.renewal_of && (UNPAID.includes(r.status) || allScheduled(r));

/**
 * The customer's carts arranged for display: each live plan with its
 * upcoming renewal (shown inside it), and the past ones (ended, fully
 * refunded). A renewal whose plan isn't live (or isn't listed) gets its own card.
 */
export function arrangeCustomPlans(carts: CustomPlanCart[]): { live: { cart: CustomPlanCart; renewal: CustomPlanCart | null }[]; past: CustomPlanCart[] } {
  const byId = new Map(carts.map((c) => [c.id, c]));
  const nested = new Set<string>();
  const renewalOf = new Map<string, CustomPlanCart>();
  for (const r of carts) {
    const parent = r.renewal_of ? byId.get(r.renewal_of) : undefined;
    if (parent && customPlanIsLive(parent) && upcomingRenewal(r) && !renewalOf.has(parent.id)) {
      renewalOf.set(parent.id, r);
      nested.add(r.id);
    }
  }
  const shown = carts.filter((c) => !nested.has(c.id));
  return {
    live: shown.filter(customPlanIsLive).map((cart) => ({ cart, renewal: renewalOf.get(cart.id) || null })),
    past: shown.filter((c) => !customPlanIsLive(c)),
  };
}

function StatusBadge({ cart }: { cart: CustomPlanCart }) {
  if (cart.status === "awaiting_payment") return <Badge tone="warning">Payment Pending</Badge>;
  if (cart.status === "activating") return <Badge tone="info">Activating…</Badge>;
  if (cart.status === "needs_review") return <Badge tone="warning">Under Review</Badge>;
  if (cart.status === "refunded") return <Badge tone="neutral">Refunded</Badge>;
  if (cart.status === "active") {
    if (cart.cars.some(carActive)) return <Badge tone="success">Active</Badge>;
    if (cart.cars.some(carScheduled)) return <Badge tone="info">Upcoming</Badge>;
    return <Badge tone="neutral">Ended</Badge>;
  }
  return <Badge tone="neutral">{toTitle(cart.status)}</Badge>;
}

const carHead = (car: CustomPlanCar) => [car.registration_number, toTitle(car.vehicle_type_name)].filter(Boolean).join(" · ") || "Car";
const washes = (n: number) => `${n} Wash${n === 1 ? "" : "es"}`;

function CarBlock({ car, paid }: { car: CustomPlanCar; paid: boolean }) {
  const pass = car.subscription;
  const refunded = isRefunded(car);
  const scheduled = paid && carScheduled(car);
  // Counts "1 Of 2 Left" only once washes can be used.
  const counting = paid && !!pass && !scheduled && !refunded;
  const ended = counting && !carActive(car);
  const extended = !!pass && ((pass.extension_days || 0) > 0 || !!pass.in_extension);
  const lastDay = pass ? lastBookingDay(pass) : "";
  const startsOn = dayLabel(car.starts_on_label, car.starts_on || pass?.start_date);
  return (
    <li className={cn("rounded-xl bg-[#F7F9FC] p-3", refunded && "opacity-90")} data-testid="custom-plan-car">
      <p className="flex min-w-0 items-center gap-2 text-sm font-semibold text-[#0E1A33]">
        <VehicleIcon vehicleTypeId={car.vehicle_type} className="h-4 w-4 shrink-0 text-[#5F6878]" />
        <span className="min-w-0 break-words tabular-nums">{carHead(car)}</span>
      </p>
      <ul className="mt-2 space-y-1">
        {car.items.map((i) => (
          <li key={i.service_id} className="flex items-baseline justify-between gap-3 text-sm">
            <span className={cn("min-w-0", refunded ? "text-[#8A94A6]" : "text-[#0E1A33]")}>{toTitle(i.service_name) || "Wash"}</span>
            <span className="shrink-0 tabular-nums text-[#5F6878]">
              {counting ? (
                <>
                  <b className="text-[#0E1A33]">{i.remaining}</b> Of {i.count} Left
                </>
              ) : (
                washes(i.count)
              )}
            </span>
          </li>
        ))}
      </ul>
      {refunded ? (
        <p className="mt-2 text-xs font-semibold text-[#1E7B3C]" data-testid="custom-plan-refunded">
          Refunded ₹{Math.round(car.refund?.amount || 0)} To Your Wallet
        </p>
      ) : scheduled ? (
        <p className="mt-2 flex items-center gap-1.5 text-xs font-semibold text-[#0A66F0]" data-testid="custom-plan-starts">
          <CalendarClock className="h-3.5 w-3.5 shrink-0" /> Starts {startsOn || "Soon"}
        </p>
      ) : (
        counting &&
        lastDay && (
          <p className={cn("mt-2 text-xs", extended && !ended ? "font-semibold text-[#0A66F0]" : "text-[#5F6878]")} data-testid="custom-plan-dates">
            {ended ? `Ended ${lastDay}` : `Last Booking Day: ${lastDay}${extended ? " · Extended" : ""}`}
          </p>
        )
      )}
    </li>
  );
}

/** The renewal inside the plan it renews: pay to renew, or the next period. */
function RenewalBlock({ renewal, standalone = false }: { renewal: CustomPlanCart; standalone?: boolean }) {
  const start = renewalStart(renewal);
  const payUrl = customPlanPayUrl(renewal);
  if (renewal.status === "active") {
    // Paid: the upcoming period, car by car.
    return (
      <section className="mt-4 rounded-xl border border-[#CFDCF0] bg-[#F5F9FF] p-3.5" data-testid="custom-plan-next-period">
        <p className="flex items-center gap-1.5 text-sm font-semibold text-[#0E1A33]">
          <CalendarClock className="h-4 w-4 shrink-0 text-[#0A66F0]" /> Next Period Starts {start || "Soon"}
        </p>
        <ul className="mt-2 space-y-2">
          {renewal.cars
            .filter((c) => !isRefunded(c))
            .map((c, i) => (
              <li key={c.vehicle_id || c.registration_number || i} className="text-sm">
                <p className="text-xs font-semibold text-[#5F6878] tabular-nums">{carHead(c)}</p>
                {c.items.map((it) => (
                  <p key={it.service_id} className="flex justify-between gap-3 text-[#0E1A33]">
                    <span className="min-w-0">{toTitle(it.service_name) || "Wash"}</span>
                    <span className="shrink-0 tabular-nums text-[#5F6878]">{washes(it.count)}</span>
                  </p>
                ))}
              </li>
            ))}
        </ul>
      </section>
    );
  }
  return (
    <section className="mt-4 rounded-xl border-2 border-[#0A66F0] bg-[#E8F0FE] p-3.5" data-testid="custom-plan-renewal">
      <p className="flex items-start gap-1.5 text-sm font-bold text-[#0E1A33]">
        <RefreshCw className="mt-0.5 h-4 w-4 shrink-0 text-[#0A66F0]" />
        <span>Renew Your Plan{start ? ` — New Period Starts ${start}` : ""}</span>
      </p>
      {!standalone && (
        <p className="mt-1 text-xs text-[#5F6878]">
          {renewal.car_count || renewal.cars.length} {(renewal.car_count || renewal.cars.length) === 1 ? "Car" : "Cars"} · {washes(renewal.washes)}
        </p>
      )}
      {renewal.status === "awaiting_payment" ? (
        payUrl ? (
          <a href={payUrl} target="_blank" rel="noopener noreferrer" className={btn("primary", "md", "mt-3 w-full")} data-testid="custom-plan-renew-pay">
            {payLabel(renewal)}
          </a>
        ) : (
          <p className="mt-2 text-sm text-[#5F6878]">Your manager will send you a payment link.</p>
        )
      ) : renewal.status === "activating" ? (
        <p className="mt-2 text-sm text-[#5F6878]">Payment received — setting up your renewal.</p>
      ) : (
        <p className="mt-2 text-sm text-[#5F6878]">We're checking your payment. Your manager will confirm soon.</p>
      )}
    </section>
  );
}

export function CustomPlanCard({ cart, renewal = null, className }: { cart: CustomPlanCart; renewal?: CustomPlanCart | null; className?: string }) {
  const paid = ["active", "activating", "refunded"].includes(cart.status);
  // Refunded cars aren't counted; money reads "₹X Paid · ₹Y Refunded"
  // (the same rule as the staff card — lib/customPlanTotals.ts).
  const totals = customPlanTotals(cart);
  const n = totals.cars;
  const payUrl = customPlanPayUrl(cart);
  const canBook = cart.status === "active" && cart.cars.some(bookableCar);
  const [booking, setBooking] = useState(false);

  // The plan's last booking day: the latest across its cars still usable.
  const liveDays = cart.cars
    .filter(carActive)
    .map((c) => c.subscription?.last_bookable_day || "")
    .filter(Boolean)
    .sort();
  const lastDay = liveDays[liveDays.length - 1] || "";
  const left = daysUntil(lastDay);
  const askToRenew = cart.status === "active" && !renewal && !cart.renewal_cart_id && left != null && left >= 0 && left <= RENEW_PROMPT_DAYS;
  const askRenew = () => {
    const plates = cart.cars.filter(carActive).map((c) => c.registration_number).filter(Boolean).join(", ");
    const lastCar = cart.cars.find((c) => carActive(c) && c.subscription?.last_bookable_day === lastDay);
    const day = lastCar?.subscription ? lastBookingDay(lastCar.subscription) : dayLabel(null, lastDay);
    openBlussitWhatsApp(`Hi Blussit, please renew my custom plan${plates ? ` (${plates})` : ""}. Last booking day: ${day}.`);
  };

  // A renewal shown on its own (the plan it renews isn't live here).
  const ownRenewal = !!cart.renewal_of && UNPAID.includes(cart.status);

  return (
    <div className={cn(card, "p-5", className)} data-testid="custom-plan-card">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[#E8F0FE] text-[#0A66F0]">
            <Layers className="h-5 w-5" />
          </span>
          <div className="min-w-0">
            <h3 className="font-display font-bold leading-tight text-[#0E1A33]">
              Custom Plan — {n} {n === 1 ? "Car" : "Cars"}
            </h3>
            <p className="mt-0.5 text-xs text-[#5F6878]">
              {washes(totals.washes)}
              {cart.total_amount ? ` · ${paidRefundedText(cart.total_amount, totals.refunded)}` : ""}
            </p>
          </div>
        </div>
        <span className="shrink-0 whitespace-nowrap">
          <StatusBadge cart={cart} />
        </span>
      </div>

      <ul className="mt-4 space-y-2">
        {cart.cars.map((car, i) => (
          <CarBlock key={car.vehicle_id || car.registration_number || i} car={car} paid={paid} />
        ))}
      </ul>

      {ownRenewal ? (
        <RenewalBlock renewal={cart} standalone />
      ) : (
        <>
          {cart.status === "awaiting_payment" &&
            (payUrl ? (
              <a href={payUrl} target="_blank" rel="noopener noreferrer" className={btn("primary", "md", "mt-4 w-full")} data-testid="custom-plan-pay">
                {payLabel(cart)}
              </a>
            ) : (
              <p className="mt-4 text-sm text-[#5F6878]">Your manager will send you a payment link.</p>
            ))}
          {cart.status === "needs_review" && <p className="mt-4 text-sm text-[#5F6878]">We're checking your payment. Your manager will confirm soon.</p>}
          {cart.status === "activating" && <p className="mt-4 text-sm text-[#5F6878]">Payment received — setting up your plan.</p>}
        </>
      )}

      {renewal && <RenewalBlock renewal={renewal} />}

      {askToRenew && (
        <div className="mt-4 flex flex-wrap items-center justify-between gap-x-3 gap-y-2 rounded-xl bg-[#F7F9FC] p-3" data-testid="custom-plan-ask-renew">
          <p className="text-sm font-semibold text-[#0E1A33]">Want To Continue?</p>
          <button type="button" onClick={askRenew} className={btn("outline", "md", "min-h-[44px]")}>
            <WhatsAppGlyph className="h-[18px] w-[18px]" /> Ask Us To Renew
          </button>
        </div>
      )}

      {canBook && (
        <>
          <button type="button" onClick={() => setBooking(true)} className={btn("primary", "md", "mt-4 w-full")} data-testid="custom-plan-book">
            Book A Plan Wash
          </button>
          <PlanWashSheet cars={carsFromCart(cart)} serviceCenterId={cart.service_center_id} open={booking} onClose={() => setBooking(false)} />
        </>
      )}
    </div>
  );
}
