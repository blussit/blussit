/**
 * Client-side mirror of the backend's plan-tier rules
 * (subscription_service.resolve_plan_price / tier_allows) — the SERVER is
 * the enforcer at plan_consumption time; this exists so the UI only ever
 * offers vehicles/subscriptions that will actually be accepted.
 *
 * A subscription is bought FOR one vehicle type (its tier) at that type's
 * plan price. It can then be redeemed on that type or any type the plan
 * prices CHEAPER — an XUV-tier card washes a sedan or hatchback, a
 * hatchback-tier card never washes an XUV. Redeeming on a cheaper type
 * still burns one full visit; the gap is never credited.
 */
import type { Service, SubscriptionPlan, UserSubscription } from "../types";

/** What this plan costs for a vehicle type — per-type override first. */
export function planPriceFor(plan: SubscriptionPlan, typeId: string | null | undefined): number {
  if (typeId) {
    if (plan.vehicle_type_discounted_prices?.[typeId] != null) return plan.vehicle_type_discounted_prices[typeId];
    if (plan.vehicle_type_prices?.[typeId] != null) return plan.vehicle_type_prices[typeId];
  }
  return plan.discounted_price ?? plan.price;
}

/** Can a subscription bought at `purchasedType` be redeemed on `candidateType`? */
export function tierAllows(plan: SubscriptionPlan | undefined, purchasedType: string | null | undefined, candidateType: string): boolean {
  if (!plan || !purchasedType || candidateType === purchasedType) return true;
  return planPriceFor(plan, candidateType) <= planPriceFor(plan, purchasedType);
}

/** Full eligibility for using `sub` on a vehicle of `typeId`: the plan must
 * cover the type AND the purchased tier must allow it. */
export function subscriptionCoversType(sub: UserSubscription, plan: SubscriptionPlan | undefined, typeId: string): boolean {
  if (plan?.vehicle_types?.length && !plan.vehicle_types.includes(typeId)) return false;
  return tierAllows(plan, sub.vehicle_type, typeId);
}

/** The cheapest tier a plan is sold at — what the plans page shows by
 * default (in practice: the hatchback price). Returns the type id too so
 * the label can say which type that price is for. */
export function cheapestTier(plan: SubscriptionPlan, candidateTypeIds: string[]): { typeId: string | null; price: number } {
  let best: { typeId: string | null; price: number } = { typeId: null, price: plan.discounted_price ?? plan.price };
  for (const id of candidateTypeIds) {
    const p = planPriceFor(plan, id);
    if (best.typeId === null || p < best.price) best = { typeId: id, price: p };
  }
  return best;
}

/**
 * Estimated real charge for a subscription-paid booking — mirrors
 * BookingService._subscription_discount: included services free; a
 * different MAIN service covered up to the cheapest included service's
 * price (never credited below zero); ADD-ONS always paid in full
 * (multiplied by quantity), plan or no plan.
 */
export function estimatePlanTopUp(
  selected: Service[],
  qtyOf: (id: string) => number,
  plan: SubscriptionPlan | undefined,
  allServices: Service[],
  priceOf: (s: Service) => number
): number {
  const includedIds = new Set(plan?.included_service_ids || []);
  const includedDocs = allServices.filter((s) => includedIds.has(s.id));
  const baseline = includedDocs.length ? Math.min(...includedDocs.map(priceOf)) : 0;
  let topUp = 0;
  for (const s of selected) {
    const price = priceOf(s) * qtyOf(s.id);
    if (includedIds.has(s.id)) continue; // fully covered
    if (s.is_addon) {
      topUp += price; // add-ons are always a real charge
    } else if (includedIds.size) {
      topUp += Math.max(0, priceOf(s) - baseline); // swap gap (qty always 1 on bases)
    }
    // legacy plan (no includedIds): main services fully waived
  }
  return Math.round(topUp);
}
