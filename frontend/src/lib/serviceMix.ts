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
