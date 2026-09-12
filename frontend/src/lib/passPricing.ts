import type { Service, SubscriptionPlan, VehicleTypeOption } from "../types";

/**
 * Client-side mirror of the backend's `resolve_pass_price`, used ONLY to
 * show a "from ₹X" on cards before the buyer has picked a car and a wash.
 * The real number always comes from POST /subscriptions/quote — this is a
 * shop-window figure, never what anyone is charged.
 *
 * Same two rules as the server, in the same order:
 *   1. an admin-set monthly price for this (wash, vehicle type) wins;
 *   2. otherwise it's the wash's STANDARD price for that type × the plan's
 *      monthly visits, less the plan discount.
 */
export function passPriceFor(
  plan: SubscriptionPlan,
  service: Service,
  vehicleTypeId: string
): number {
  const override = plan.service_pass_prices?.[service.id]?.[vehicleTypeId];
  if (override != null) return Math.round(override);
  // Standard price, never `discounted_price` — that's the first-visit offer.
  const perWash = service.vehicle_type_prices?.[vehicleTypeId] ?? service.price;
  const visits = plan.total_service_count || 1;
  const discount = plan.plan_discount_percent || 0;
  return Math.round((perWash * visits * (100 - discount)) / 100);
}

/** The cheapest this plan can come to, across every wash it sells and every
 *  vehicle type it's sold for. Null when we can't work it out yet. */
export function passFromPrice(
  plan: SubscriptionPlan,
  services: Service[],
  vehicleTypes: VehicleTypeOption[] | undefined
): number | null {
  const menu = (plan.included_service_ids ?? [])
    .map((id) => services.find((s) => s.id === id))
    .filter((s): s is Service => !!s && !s.is_addon);
  if (!menu.length) return null;

  const types = plan.vehicle_types?.length
    ? plan.vehicle_types
    : (vehicleTypes ?? []).filter((t) => t.is_active !== false).map((t) => t.id);

  const candidates: number[] = [];
  for (const service of menu) {
    // A wash restricted to certain vehicle types can only be priced for
    // those — pricing a bike wash at SUV rates would invent a number.
    const allowed = service.vehicle_types?.length ? types.filter((t) => service.vehicle_types!.includes(t)) : types;
    for (const typeId of allowed) candidates.push(passPriceFor(plan, service, typeId));
    if (!allowed.length && !types.length) candidates.push(passPriceFor(plan, service, ""));
  }
  return candidates.length ? Math.min(...candidates) : null;
}
