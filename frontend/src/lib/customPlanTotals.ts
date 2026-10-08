/**
 * A custom multi-car plan's header numbers, the same on the customer card
 * and the staff card (FINAL-POLISH 2026-10-08):
 *
 *   - cars / washes: only the cars still on the plan — a refunded car's
 *     washes went back to the wallet, so they aren't counted (a cart where
 *     every car was refunded keeps its full counts, so it never reads "0").
 *   - money: what was paid stays the paid amount; refunds are shown beside
 *     it — "₹1,499 Paid · ₹300 Refunded" — never netted silently.
 */
interface CartLike {
  cars: { status?: string | null; washes?: number | null }[];
  car_count?: number | null;
  washes?: number | null;
  refunded_amount?: number | null;
}

export interface PlanTotals {
  cars: number;
  washes: number;
  refundedCars: number;
  refunded: number;
}

export function customPlanTotals(cart: CartLike): PlanTotals {
  const kept = cart.cars.filter((c) => c.status !== "refunded");
  const refundedCars = cart.cars.length - kept.length;
  const everyCarRefunded = cart.cars.length > 0 && kept.length === 0;
  const all = { cars: cart.car_count || cart.cars.length, washes: Number(cart.washes || 0) };
  return {
    cars: refundedCars && !everyCarRefunded ? kept.length : all.cars,
    washes: refundedCars && !everyCarRefunded ? kept.reduce((n, c) => n + Number(c.washes || 0), 0) : all.washes,
    refundedCars,
    refunded: Math.round(Number(cart.refunded_amount || 0)),
  };
}

export const rupeesIN = (n?: number | null) => `₹${Math.round(Number(n || 0)).toLocaleString("en-IN")}`;

/** "₹1,499 Paid · ₹300 Refunded" (refunds only when there are any). */
export const paidRefundedText = (paid: number | null | undefined, refunded: number) =>
  refunded > 0 ? `${rupeesIN(paid)} Paid · ${rupeesIN(refunded)} Refunded` : rupeesIN(paid);
