/**
 * Client-side mirror of the backend's service-mix rules
 * (BookingService._validate_service_mix). The SERVER is the enforcer —
 * this module exists so the UI only ever OFFERS valid combinations:
 *
 *  - add-ons attach to a main service, never bookable alone;
 *  - car add-ons show with car services, bike add-ons with bike washes —
 *    except the deliberate combo: a car booking may ADD bikes (Extra Bike
 *    Wash, per bike) and polish exactly those bikes;
 *  - bike-wash variants ("1 bike"…"5 bikes") collapse into one service
 *    with a "how many bikes?" count — never sibling chips;
 *  - bike polish is per bike and can't exceed the bikes in the booking.
 */
import type { Service } from "../types";

export const BIKE_WORD = /bike|scooter|two.?wheeler/i;

export interface VehicleTypeLike {
  id: string;
  name: string;
}

export function bikeTypeIds(types: VehicleTypeLike[] | undefined): Set<string> {
  return new Set((types || []).filter((t) => BIKE_WORD.test(t.name)).map((t) => t.id));
}

export function eligibleFor(s: Service, typeId: string): boolean {
  return !s.vehicle_types?.length || s.vehicle_types.includes(typeId);
}

/** "Bike Wash (3 bikes)" -> 3; non-variant services count as 1. */
export function variantCount(s: Service): number {
  const m = /\d+/.exec(s.variant_label || "");
  return m ? parseInt(m[0], 10) : 1;
}

export interface BaseGroup {
  key: string;
  label: string;
  primary: Service;
  variants: Service[]; // sorted by count; length 1 for plain services
}

/** Non-add-on services for this vehicle type, variant groups collapsed. */
export function baseGroups(services: Service[], typeId: string): BaseGroup[] {
  const seen = new Map<string, BaseGroup>();
  const out: BaseGroup[] = [];
  for (const s of services) {
    if (s.is_addon || !eligibleFor(s, typeId)) continue;
    const key = s.variant_group || s.id;
    const existing = seen.get(key);
    if (existing) {
      existing.variants.push(s);
      continue;
    }
    const g: BaseGroup = { key, label: s.variant_group ? s.name.split("(")[0].trim() : s.name, primary: s, variants: [s] };
    seen.set(key, g);
    out.push(g);
  }
  for (const g of out) {
    g.variants.sort((a, b) => variantCount(a) - variantCount(b));
    g.primary = g.variants[0];
  }
  return out;
}

export interface AddonKit {
  /** one-shot add-ons for the booking's own class (e.g. Exterior Polish on a car) */
  simple: Service[];
  /** the car-class "add a bike to this visit" per-bike line (Extra Bike Wash) */
  addBike: Service | null;
  /** the bike-class per-bike polish */
  bikePolish: Service | null;
}

export function addonKit(services: Service[], typeId: string, bikeIds: Set<string>): AddonKit {
  const isBike = bikeIds.has(typeId);
  const addons = services.filter((s) => s.is_addon);
  const bikePolish =
    addons.find((s) => (s.vehicle_types || []).some((i) => bikeIds.has(i)) && /polish/i.test(s.name)) || null;
  // The add-a-bike line rides on BOTH classes: ₹60/bike added to a car
  // wash, and the same line powers the bike booking's −/+ counter
  // (base wash + N extra bikes).
  const addBike = addons.find((s) => BIKE_WORD.test(s.name) && !/polish/i.test(s.name)) || null;
  if (isBike) {
    const simple = addons.filter((s) => eligibleFor(s, typeId) && s !== bikePolish && s !== addBike);
    return { simple, addBike, bikePolish };
  }
  const carAddons = addons.filter((s) => eligibleFor(s, typeId));
  const simple = carAddons.filter((s) => s !== addBike);
  return { simple, addBike, bikePolish };
}

/**
 * The add-ons a line of this vehicle type is OFFERED — the booking page's
 * own chips (`kit.simple`, plus bike polish on a bike). The one filter every
 * add-on picker uses: booking page, Book A Plan Wash, Edit Booking, the
 * captain's and the manager's Add Service (QA 2026-10-07 — they offered
 * Extra Bike Wash on cars). The add-a-bike line is never a chip: on a bike
 * booking it is the bike counter. The server refuses the same on edits and
 * on-site adds (BookingService._ensure_offered_for_type).
 */
export function offeredAddons(services: Service[], typeId: string, bikeIds: Set<string> = new Set()): Service[] {
  const kit = addonKit(services, typeId, bikeIds);
  return bikeIds.has(typeId) && kit.bikePolish ? [...kit.simple, kit.bikePolish] : kit.simple;
}

/**
 * A bike line as the booking page builds it (QuickBookFlow `lineFor`): the
 * bike COUNT picks the group's exact variant ("Bike Wash (2 bikes)"), else
 * its smallest variant plus the add-a-bike line × the rest; bike polish is
 * per bike (× count); every other add-on is one per vehicle.
 *
 * `fixedBase` keeps the base as given (a plan wash: the pass's own
 * service) — any extra bikes then ride as the add-a-bike line.
 * Used by the customer's Edit Booking and Book A Plan Wash sheets; the
 * booking page keeps its own copy of the same rule.
 */
export function composeBikeLine({
  variants,
  count,
  kit,
  addonIds,
  fixedBase,
}: {
  /** The base's variant group, smallest first (or just the base). */
  variants: Service[];
  count: number;
  kit: AddonKit;
  /** Picked add-ons (the add-a-bike line is ignored here — it's the count). */
  addonIds: string[];
  fixedBase?: Service | null;
}): { base: Service | null; extraBikes: number; serviceIds: string[]; quantities: Record<string, number> } {
  const sorted = [...variants].sort((a, b) => variantCount(a) - variantCount(b));
  const exact = fixedBase ? null : sorted.find((v) => variantCount(v) === count);
  const base = fixedBase || exact || sorted[0] || null;
  if (!base) return { base: null, extraBikes: 0, serviceIds: [], quantities: {} };
  const extraBikes = !exact && kit.addBike ? Math.max(0, count - variantCount(base)) : 0;
  const serviceIds = [base.id];
  const quantities: Record<string, number> = {};
  if (extraBikes > 0 && kit.addBike) {
    serviceIds.push(kit.addBike.id);
    quantities[kit.addBike.id] = extraBikes;
  }
  const bikes = variantCount(base) + extraBikes;
  for (const id of addonIds) {
    if (id === base.id || id === kit.addBike?.id || serviceIds.includes(id)) continue;
    serviceIds.push(id);
    if (kit.bikePolish && id === kit.bikePolish.id && bikes > 1) quantities[id] = bikes;
  }
  return { base, extraBikes, serviceIds, quantities };
}

/** The −/+ range of a bike line's counter: from the smallest variant (or
 *  the fixed base) up to 10 when the add-a-bike line exists, else up to
 *  the largest variant. min === max means no counter to show. */
export function bikeCountRange(variants: Service[], kit: AddonKit, fixedBase?: Service | null): { min: number; max: number } {
  const counts = (fixedBase ? [fixedBase] : variants).map(variantCount);
  const min = counts.length ? Math.min(...counts) : 1;
  const max = kit.addBike ? 10 : counts.length ? Math.max(...counts) : 1;
  return { min, max: Math.max(min, max) };
}
