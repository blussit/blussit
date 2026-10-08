import type { GarageCar } from "../../api/profile";
import { titleCase } from "../public/landing/shared";
import { societyPlanSuffix, type BookingSlab } from "../../lib/bookingGroups";
import { vehicleLabel } from "../../lib/constants";
import { formatShortDate } from "../../lib/date";
import type { Booking, VehicleTypeOption } from "../../types";

/** The query every "your cars" surface shares. Under "my-bookings" on
 *  purpose: anything that invalidates the customer's bookings (a new
 *  booking, a cancel, a payment) refreshes the garage too. */
export const GARAGE_QUERY_KEY = ["my-bookings", "garage"] as const;

/**
 * Where "Clean again" / "Book wash" for one car goes: its newest finished
 * single-car booking is replayed (?repeat= fills the car, services and
 * address — only the time is left), otherwise a fresh booking for its
 * vehicle type (?type=).
 */
export function garageRebookPath(car: GarageCar): string {
  if (car.repeat_booking_id) return `/app/book?repeat=${car.repeat_booking_id}`;
  return car.vehicle_type ? `/app/book?type=${encodeURIComponent(car.vehicle_type)}` : "/app/book";
}

/** A saved car by its make ("Honda City"); a booked-only car by its plate
 *  when it has one, else just its type ("XUV 5-Seater"). */
export function garageTitle(car: GarageCar): string {
  const makeModel = [car.brand, car.model].filter(Boolean).join(" ").trim();
  return makeModel || car.registration_number || titleCase(car.vehicle_type_name) || "My Car";
}

/** The type, when the title isn't already it ("Sedan" under "Honda City"). */
export function garageTypeLine(car: GarageCar): string {
  const title = garageTitle(car);
  const type = titleCase(car.vehicle_type_name);
  return type && type !== title ? type : "";
}

/** The plate, when the title isn't already it. */
export function garagePlate(car: GarageCar): string {
  return car.registration_number && car.registration_number !== garageTitle(car) ? car.registration_number : "";
}

/** "Next Wash 5 Oct · Star Wash" — the date, then that visit's service when known. */
const withService = (text: string, service?: string) => (service ? `${text} · ${service}` : text);

/** "Next Wash 5 Oct · Last Washed 3 Oct" — whichever of the two exist. */
export function garageWashLine(car: GarageCar): string {
  const parts: string[] = [];
  if (car.next_wash_on) parts.push(`Next Wash ${formatShortDate(car.next_wash_on)}`);
  if (car.last_washed_on) parts.push(`Last Washed ${formatShortDate(car.last_washed_on)}`);
  return parts.join(" · ");
}

/** Per garage car, the service on its next visit and on its last wash (see useGarageServices). */
export type GarageServices = Record<string, { next?: string; last?: string }>;

/** The garage's two dates, each with its own service: ["Next Wash 5 Oct · Star Wash", "Last Washed 3 Oct · Jet Wash"]. */
export function garageWashLines(car: GarageCar, services: GarageServices = {}): string[] {
  const lines: string[] = [];
  if (car.next_wash_on) lines.push(withService(`Next Wash ${formatShortDate(car.next_wash_on)}`, services[car.id]?.next));
  if (car.last_washed_on) lines.push(withService(`Last Washed ${formatShortDate(car.last_washed_on)}`, services[car.id]?.last));
  return lines;
}

/** One date for tight rows: the next wash, else the last one — with its service when known. */
export function garageNextOrLast(car: GarageCar, services: GarageServices = {}): string {
  if (car.next_wash_on) return withService(`Next Wash ${formatShortDate(car.next_wash_on)}`, services[car.id]?.next);
  return car.last_washed_on ? withService(`Last Washed ${formatShortDate(car.last_washed_on)}`, services[car.id]?.last) : "";
}

/** "Clean Again" once the car has been washed, "Book Wash" before that. */
export const garageCta = (car: GarageCar) => (car.wash_count > 0 ? "Clean Again" : "Book Wash");

export const typeNameOf = (types: VehicleTypeOption[] | undefined, id?: string | null) => titleCase(types?.find((t) => t.id === id)?.name);

/* ------------------------------------------------------------------ */
/* Car type + service on every booking surface                        */
/* ------------------------------------------------------------------ */

/** A booking's service as a title: "Star Wash" (+ " · Society plan (…)" on a society pass). */
export const bookingServiceTitle = (b: Booking, fallback = "Service") =>
  `${titleCase(b.combo_name || b.service_names?.join(", ") || fallback)}${societyPlanSuffix(b)}`;

/** A booking's car type: "Sedan" — the server's type name, else the type id looked up, else the quick-booking label. */
export const bookingTypeName = (b: Booking, types?: VehicleTypeOption[]) =>
  titleCase(b.vehicle_type_name) ||
  typeNameOf(types, b.vehicle_type || b.vehicle_snapshot?.vehicle_type) ||
  (b.vehicle_snapshot?.brand || b.vehicle_snapshot?.model ? "" : titleCase(b.vehicle_label));

/** "Sedan · Star Wash" for one car; "2 × Sedan · Star Wash" or "Sedan · Star Wash + SUV · Deep Cleaning" for a visit. */
export function visitTypeServiceLabel(slab: Pick<BookingSlab, "bookings">, types?: VehicleTypeOption[]): string {
  const counts = new Map<string, number>();
  for (const b of slab.bookings) {
    const key = [bookingTypeName(b, types), bookingServiceTitle(b)].filter(Boolean).join(" · ");
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return Array.from(counts, ([key, n]) => (n > 1 ? `${n} × ${key}` : key)).join(" + ");
}

/** Each distinct service of a visit once, as titles: "Star Wash" / "Star Wash + Deep Cleaning". */
export const visitServiceTitle = (slab: Pick<BookingSlab, "bookings">) => Array.from(new Set(slab.bookings.map((b) => bookingServiceTitle(b)))).join(" + ");

/** "Sedan" / "2 × Sedan" / "Sedan + SUV" — just the car types of a visit. */
export function visitTypeLabel(slab: Pick<BookingSlab, "bookings">, types?: VehicleTypeOption[]): string {
  const counts = new Map<string, number>();
  for (const b of slab.bookings) {
    const key = bookingTypeName(b, types) || "Vehicle";
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return Array.from(counts, ([key, n]) => (n > 1 ? `${n} × ${key}` : key)).join(" + ");
}

/** The make / plate a visit's cars carry beyond their type ("Honda City · MP09AB1234"); "" for type-only bookings. */
export function visitCarDetail(slab: Pick<BookingSlab, "bookings">, types?: VehicleTypeOption[]): string {
  const extra = slab.bookings
    .map((b) => {
      const label = vehicleLabel(b);
      return label === "Vehicle" || label.toLowerCase() === bookingTypeName(b, types).toLowerCase() ? "" : label;
    })
    .filter(Boolean);
  return Array.from(new Set(extra)).join(" + ");
}


/**
 * The service on each garage car's next visit and last wash — sent with the
 * garage rows themselves (next_service_name / last_wash_service_name), so the
 * page needs no extra request per car. `which` is kept for callers.
 */
export function useGarageServices(cars: GarageCar[] | undefined, _which: "both" | "nextOrLast" = "both"): GarageServices {
  const out: GarageServices = {};
  for (const car of cars || []) {
    out[car.id] = {
      next: car.next_service_name ? titleCase(car.next_service_name) : undefined,
      last: car.last_wash_service_name ? titleCase(car.last_wash_service_name) : undefined,
    };
  }
  return out;
}
